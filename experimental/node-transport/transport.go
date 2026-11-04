// Package transport is an experimental authenticated datagram format, not QUIC.
package transport

import (
	"bytes"
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
	"sort"
	"sync"
	"time"
)

const (
	MaxPayload  = 1380
	MaxPacket   = 1452
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
	mu             sync.Mutex
	keys           keys
	salt           [16]byte
	sequence       uint64
	cachedSlot     int64
	cachedID       [8]byte
	cachedAEAD     cipher.AEAD
	nonce          [12]byte
	random         [4096]byte
	randomUsed     int
	randomReady    bool
	segmentPadding bool
	bucketLength   int
	bucketCount    int
	buckets        [5]int
}

// Caller serializes access; buffered CSPRNG bytes are used once, only for padding.
func (s *Sender) paddingRandom(dst []byte) error {
	for len(dst) > 0 {
		if !s.randomReady || s.randomUsed == len(s.random) {
			if _, err := rand.Read(s.random[:]); err != nil {
				return err
			}
			s.randomUsed = 0
			s.randomReady = true
		}
		n := copy(dst, s.random[s.randomUsed:])
		s.randomUsed += n
		dst = dst[n:]
	}
	return nil
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

func (s *Sender) Seal(payload []byte, now time.Time) ([]byte, error) {
	return s.SealInto(make([]byte, 0, MaxPacket), make([]byte, 2048), payload, now)
}

// SealInto reuses caller-owned buffers. Neither may overlap payload.
func (s *Sender) SealInto(dst, scratch, payload []byte, now time.Time) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.sealOwned(dst, scratch, payload, now)
}

// sealOwned requires the mutex or exclusive ownership of this Sender.
func (s *Sender) sealOwned(dst, scratch, payload []byte, now time.Time) ([]byte, error) {
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
	if cap(dst) < MaxPacket || len(scratch) < 2048 {
		return nil, ErrPacket
	}
	header := dst[:HeaderSize]
	header[0] = 0x43
	copy(header[1:9], id[:])
	binary.BigEndian.PutUint32(header[9:13], uint32(s.sequence))
	copy(header[13:], s.salt[:])
	var random [1]byte
	padding := 0
	if s.segmentPadding {
		if s.bucketCount == 0 || s.bucketLength != len(payload) {
			s.bucketLength, s.bucketCount = len(payload), 0
			minimum := HeaderSize + 16 + 2 + len(payload) + 16
			maximum := min(MaxPacket, HeaderSize+16+2+len(payload)+128)
			for size := (minimum + 31) / 32 * 32; size <= maximum; size += 32 {
				s.buckets[s.bucketCount] = size
				s.bucketCount++
			}
			if maximum == MaxPacket && (s.bucketCount == 0 || s.buckets[s.bucketCount-1] != maximum) {
				s.buckets[s.bucketCount] = maximum
				s.bucketCount++
			}
			if s.bucketCount == 0 {
				return nil, ErrPacket
			}
		}
		choice := 0
		if s.bucketCount > 1 {
			for {
				if err := s.paddingRandom(random[:]); err != nil {
					return nil, err
				}
				if int(random[0]) < 256-256%s.bucketCount {
					break
				}
			}
			choice = int(random[0]) % s.bucketCount
		}
		padding = s.buckets[choice] - HeaderSize - 16 - 2 - len(payload)
	} else {
		for {
			if err := s.paddingRandom(random[:]); err != nil {
				return nil, err
			}
			if random[0] < 226 {
				break
			}
		}
		padding = 16 + int(random[0]%113)
		if maximum := MaxPacket - HeaderSize - 16 - 2 - len(payload); padding > maximum {
			padding = maximum
		}
	}
	plain := scratch[:2+len(payload)+padding]
	binary.BigEndian.PutUint16(plain, uint16(len(payload)))
	copy(plain[2:], payload)
	if err := s.paddingRandom(plain[2+len(payload):]); err != nil {
		return nil, err
	}
	binary.BigEndian.PutUint64(s.nonce[4:], s.sequence)
	// Consume the counter before returning any ciphertext, never wrap under a key.
	s.sequence++
	return s.cachedAEAD.Seal(header, s.nonce[:], plain, header), nil
}

func (s *Sender) Port(now time.Time, base, count int) (int, error) {
	return s.keys.port(now, base, count)
}

func (r *Receiver) Port(now time.Time, base, count int) (int, error) {
	return r.keys.port(now, base, count)
}

func (k *keys) port(now time.Time, base, count int) (int, error) {
	if base < 1024 || count < 1 || count > 1024 || base+count > 65536 || now.Unix() < 0 {
		return 0, errors.New("invalid hopping range")
	}
	slot := now.Unix() / k.interval
	if count == 1 {
		return base, nil
	}
	if count == 2 {
		return base + (int(slot&1) ^ int(mac(k.hop, 0)[0]&1)), nil
	}
	block := slot / int64(count)
	order := k.portOrder(block, count)
	// Swapping only the first two entries leaves the last entry unchanged.
	// The preceding block's final port is therefore computable without recursion.
	if block > 0 && order[0].index == k.portOrder(block-1, count)[count-1].index {
		order[0], order[1] = order[1], order[0]
	}
	return base + order[int(slot%int64(count))].index, nil
}

type portRank struct {
	index  int
	digest [32]byte
}

func (k *keys) portOrder(block int64, count int) []portRank {
	order := make([]portRank, count)
	var input [16]byte
	binary.BigEndian.PutUint64(input[:8], uint64(block))
	binary.BigEndian.PutUint32(input[8:12], uint32(count))
	for i := range order {
		order[i].index = i
		binary.BigEndian.PutUint32(input[12:], uint32(i))
		h := hmac.New(sha256.New, k.hop)
		h.Write(input[:])
		h.Sum(order[i].digest[:0])
	}
	sort.Slice(order, func(i, j int) bool { return bytes.Compare(order[i].digest[:], order[j].digest[:]) < 0 })
	return order
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

// Receiver state is session-local. A restarted receiver MUST use a fresh master
// key. ClaimReceiverKey enforces this in the demo; persistent replay state and
// authenticated key exchange are deliberately not claimed by this prototype.
type Receiver struct {
	mu      sync.Mutex
	keys    keys
	epoch   int64
	ids     map[[8]byte]int64
	windows map[[24]byte]*window
	nonce   [12]byte
}

func NewReceiver(master []byte, direction string, interval time.Duration) (*Receiver, error) {
	k, err := newKeys(master, direction, interval)
	if err != nil {
		return nil, err
	}
	return &Receiver{keys: k, epoch: -1, windows: make(map[[24]byte]*window)}, nil
}

func (r *Receiver) Open(packet []byte, now time.Time) ([]byte, error) {
	return r.OpenInto(nil, packet, now)
}

// OpenInto releases plaintext only after authentication and replay checks.
func (r *Receiver) OpenInto(dst, packet []byte, now time.Time) ([]byte, error) {
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
	if !ok {
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
	binary.BigEndian.PutUint64(r.nonce[4:], uint64(number))
	plain, err := aead.Open(dst[:0], r.nonce[:], packet[HeaderSize:], packet[:HeaderSize])
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
	if !w.accept(number) {
		return nil, ErrPacket
	}
	if !exists {
		r.windows[key] = w
	}
	return plain[2 : 2+size], nil
}
