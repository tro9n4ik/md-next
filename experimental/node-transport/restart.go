package transport

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// slotStore reserves a slot on disk before any packet from it is delivered.
// A new process drops all slots through the previous durable reservation.
type slotStore struct {
	lock        *os.File
	path        string
	floor, high int64
	failed      error
}

// NewPersistentReceiver permits reuse of the same secret after restart on Linux.
// Keep the private directory on persistent storage, and close the receiver when
// done. Deleting or rolling back state requires rotating the secret on both peers.
func NewPersistentReceiver(master []byte, direction string, interval time.Duration, dir string) (*Receiver, error) {
	r, err := NewReceiver(master, direction, interval)
	if err != nil {
		return nil, err
	}
	if err := os.Mkdir(dir, 0700); err != nil && !errors.Is(err, os.ErrExist) {
		return nil, err
	}
	info, err := os.Lstat(dir)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("private state directory required")
	}
	if err := syncDirectory(filepath.Dir(filepath.Clean(dir))); err != nil {
		return nil, err
	}
	id := receiverKeyID(master, direction)
	lock, err := lockSlotStore(filepath.Join(dir, id+".lock"))
	if err != nil {
		return nil, err
	}
	store := &slotStore{lock: lock, path: filepath.Join(dir, id+".slot"), floor: -1, high: -1}
	if err := store.load(filepath.Join(dir, id+".used")); err != nil {
		lock.Close()
		return nil, err
	}
	r.store = store
	return r, nil
}

func (s *slotStore) load(legacy string) error {
	if _, err := os.Lstat(legacy); !errors.Is(err, os.ErrNotExist) {
		return errors.New("legacy receiver state: rotate the shared secret on both peers")
	}
	info, err := os.Lstat(s.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 32 {
		return errors.New("invalid receiver slot file")
	}
	data, err := os.ReadFile(s.path)
	if err != nil {
		return err
	}
	slot, err := strconv.ParseInt(strings.TrimSpace(string(data)), 10, 64)
	if err != nil || slot < 0 {
		return errors.New("invalid receiver slot")
	}
	s.floor, s.high = slot, slot
	return nil
}

func (s *slotStore) reserve(slot int64) error {
	if s.failed != nil {
		return s.failed
	}
	if s.lock == nil {
		return errors.New("receiver closed")
	}
	if slot <= s.high {
		return nil
	}
	if err := s.save(slot); err != nil {
		s.failed = fmt.Errorf("receiver reservation failed: %w", err)
		return s.failed
	}
	s.high = slot
	return nil
}

func (s *slotStore) save(slot int64) error {
	dir := filepath.Dir(s.path)
	f, err := os.CreateTemp(dir, ".slot-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	_, err = fmt.Fprintln(f, slot)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Rename(f.Name(), s.path); err != nil {
		return err
	}
	return syncDirectory(dir)
}

func syncDirectory(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

// Close releases the exclusive state lock. A closed persistent receiver rejects packets.
func (r *Receiver) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.store == nil || r.store.lock == nil {
		return nil
	}
	err := r.store.lock.Close()
	r.store.lock = nil
	return err
}
