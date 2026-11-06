// Package transport is an experimental authenticated datagram format, not QUIC.
package transport

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"math/big"
	"sync"
	"time"
)

const (
	MaxPayload  = 1024
	MaxPacket   = 1200
	HeaderSize  = 29 // flags(1), CID(8), PN(4), session salt(16)
	MaxSessions = 1024
)

var ErrPacket = errors.New("invalid, unauthenticated, expired or replayed datagram")

type keys struct {
	master    [32]byte
	cid, hop  []byte
	direction string
	interval  int64
}

func newKeys(master []byte, direction string, interval time.Duration) (keys, error) {
	if len(master) != 32 || (direction != "uplink" && direction != "downlink") || interval < time.Second || interval > time.Minute {
		return keys{}, errors.New("32-byte key, direction and interval 1s..60s required")
	}
	k := keys{direction: direction, interval: int64(interval / time.Second)}
	copy(k.master[:], master)
	var err error
	k.cid, err = hkdf.Key(sha256.New, master, nil, "mdnext/datagram/v1/cid/"+direction, 32)
	if err != nil {
		return keys{}, err
	}
	k.hop, err = hkdf.Key(sha256.New, master, nil, "mdnext/datagram/v1/hop/"+direction, 32)
	return k, err
}

func mac(key []byte, slot int64) []byte {
	var b [8]byte
	binary.BigEndian.PutUint64(b[:], uint64(slot))
	h := hmac.New(sha256.New, key)
	h.Write(b[:])
	return h.Sum(nil)
}

func (k *keys) id(slot int64) (id [8]byte) { copy(id[:], mac(k.cid, slot)); return }
func (k *keys) aead(slot int64, salt []byte) (cipher.AEAD, error) {
	key, err := hkdf.Key(sha256.New, k.master[:], salt, fmt.Sprintf("mdnext/datagram/v1/aead/%s/%d", k.direction, slot), 16)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

type Sender struct {
	mu         sync.Mutex
	keys       keys
	salt       [16]byte
	sequence   uint64
	cachedSlot int64
	cachedID   [8]byte
	cachedAEAD cipher.AEAD
}

func NewSender(master []byte, direction string, interval time.Duration) (*Sender, error) {
	k, err := newKeys(master, direction, interval)
	if err != nil {
		return nil, err
	}
	s := &Sender{keys: k}
	_, err = rand.Read(s.salt[:])
	return s, err
}

func nonce(sequence uint32) (n [12]byte) { binary.BigEndian.PutUint64(n[4:], uint64(sequence)); return }

func (s *Sender) Seal(payload []byte, now time.Time) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(payload) > MaxPayload || now.Unix() < 0 {
		return nil, ErrPacket
	}
	if s.sequence > math.MaxUint32 {
		if _, err := rand.Read(s.salt[:]); err != nil {
			return nil, err
		}
		s.sequence = 0
		s.cachedAEAD = nil
	}
	slot := now.Unix() / s.keys.interval
	if s.cachedAEAD == nil || s.cachedSlot != slot {
		aead, err := s.keys.aead(slot, s.salt[:])
		if err != nil {
			return nil, err
		}
		s.cachedAEAD = aead
		s.cachedSlot = slot
		s.cachedID = s.keys.id(slot)
	}
	id := s.cachedID
	header := make([]byte, HeaderSize, MaxPacket)
	header[0] = 0x43
	copy(header[1:9], id[:])
	binary.BigEndian.PutUint32(header[9:13], uint32(s.sequence))
	copy(header[13:], s.salt[:])
	size, err := rand.Int(rand.Reader, big.NewInt(113))
	if err != nil {
		return nil, err
	}
	plain := make([]byte, 2+len(payload)+16+int(size.Int64()))
	binary.BigEndian.PutUint16(plain, uint16(len(payload)))
	copy(plain[2:], payload)
	if _, err = rand.Read(plain[2+len(payload):]); err != nil {
		return nil, err
	}
	n := nonce(uint32(s.sequence))
	// Consume the counter before returning any ciphertext, never wrap under a key.
	s.sequence++
	return s.cachedAEAD.Seal(header, n[:], plain, header), nil
}

func (s *Sender) Port(now time.Time, base, count int) (int, error) {
	if base < 1024 || count < 1 || count > 1024 || base+count > 65536 || now.Unix() < 0 {
		return 0, errors.New("invalid hopping range")
	}
	digest := mac(s.keys.hop, now.Unix()/s.keys.interval)
	return base + int(binary.BigEndian.Uint64(digest[:8])%uint64(count)), nil
}

type window struct {
	highest uint32
	bits    uint64
	slot    int64
	aead    cipher.AEAD
}

func (w *window) accept(n uint32) bool {
	if w.bits == 0 {
		w.highest = n
		w.bits = 1
		return true
	}
	if n > w.highest {
		delta := n - w.highest
		if delta >= 64 {
			w.bits = 0
		} else {
			w.bits <<= delta
		}
		w.highest = n
		w.bits |= 1
		return true
	}
	delta := w.highest - n
	if delta >= 64 || w.bits&(uint64(1)<<delta) != 0 {
		return false
	}
	w.bits |= uint64(1) << delta
	return true
}

// Receiver keeps packet windows in memory. Use NewPersistentReceiver to retain
// the slot boundary across restarts, or use a fresh master key after restart.
type Receiver struct {
	mu      sync.Mutex
	keys    keys
	epoch   int64
	ids     map[[8]byte]int64
	windows map[[24]byte]*window
	store   *slotStore
}

func NewReceiver(master []byte, direction string, interval time.Duration) (*Receiver, error) {
	k, err := newKeys(master, direction, interval)
	if err != nil {
		return nil, err
	}
	return &Receiver{keys: k, epoch: -1, windows: make(map[[24]byte]*window)}, nil
}

func (r *Receiver) Open(packet []byte, now time.Time) ([]byte, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(packet) < HeaderSize+16+18 || len(packet) > MaxPacket || packet[0] != 0x43 || now.Unix() < 0 {
		return nil, ErrPacket
	}
	epoch := now.Unix() / r.keys.interval
	if epoch < r.epoch {
		epoch = r.epoch
	} // Clock rollback cannot reopen expired slots.
	if r.ids == nil || epoch != r.epoch {
		r.epoch = epoch
		r.ids = make(map[[8]byte]int64, 3)
		for slot := epoch - 1; slot <= epoch+1; slot++ {
			if slot >= 0 {
				r.ids[r.keys.id(slot)] = slot
			}
		}
		for key, w := range r.windows {
			if w.slot < epoch-1 {
				delete(r.windows, key)
			}
		}
	}
	var id [8]byte
	copy(id[:], packet[1:9])
	slot, ok := r.ids[id]
	if !ok || (r.store != nil && slot <= r.store.floor) {
		return nil, ErrPacket
	}
	var key [24]byte
	copy(key[:16], packet[13:29])
	binary.BigEndian.PutUint64(key[16:], uint64(slot))
	w, exists := r.windows[key]
	var aead cipher.AEAD
	if exists {
		aead = w.aead
	} else {
		var err error
		aead, err = r.keys.aead(slot, packet[13:29])
		if err != nil {
			return nil, ErrPacket
		}
	}
	number := binary.BigEndian.Uint32(packet[9:13])
	n := nonce(number)
	plain, err := aead.Open(nil, n[:], packet[HeaderSize:], packet[:HeaderSize])
	if err != nil {
		return nil, ErrPacket
	}
	size := int(binary.BigEndian.Uint16(plain))
	padding := len(plain) - 2 - size
	if size > MaxPayload || padding < 16 || padding > 128 {
		return nil, ErrPacket
	}
	if !exists {
		if len(r.windows) >= MaxSessions {
			return nil, ErrPacket
		}
		w = &window{slot: slot, aead: aead}
	}
	next := *w
	if !next.accept(number) {
		return nil, ErrPacket
	}
	if r.store != nil {
		if err := r.store.reserve(slot); err != nil {
			return nil, err
		}
	}
	*w = next
	if !exists {
		r.windows[key] = w
	}
	return plain[2 : 2+size], nil
}
