package transport

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestPersistentReceiverRestart(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "state")
	interval := 25 * time.Second
	now := time.Unix(1750000000, 0)
	s, err := NewSender(testKey, "uplink", interval)
	if err != nil {
		t.Fatal(err)
	}
	open := func() (*Receiver, error) { return NewPersistentReceiver(testKey, "uplink", interval, dir) }
	r, err := open()
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if other, err := open(); err == nil {
		other.Close()
		t.Fatal("a second receiver acquired the same state")
	}
	current := packet(t, s, "current", now)
	next := packet(t, s, "ahead", now.Add(interval))
	forged := append([]byte(nil), next...)
	forged[len(forged)-1] ^= 1
	if _, err := r.Open(forged, now); !errors.Is(err, ErrPacket) {
		t.Fatal(err)
	}
	if r.store.high != -1 {
		t.Fatal("unauthenticated packet advanced state")
	}
	for _, p := range [][]byte{next, current} {
		if _, err := r.Open(p, now); err != nil {
			t.Fatal(err)
		}
	}
	if err := r.Close(); err != nil {
		t.Fatal(err)
	}
	restarted, err := open()
	if err != nil {
		t.Fatal(err)
	}
	defer restarted.Close()
	for _, p := range [][]byte{current, next, packet(t, s, "new but reserved slot", now.Add(interval))} {
		if _, err := restarted.Open(p, now.Add(interval)); !errors.Is(err, ErrPacket) {
			t.Fatalf("reserved slot reopened: %v", err)
		}
	}
	fresh := packet(t, s, "new slot", now.Add(2*interval))
	if _, err := restarted.Open(fresh, now.Add(2*interval)); err != nil {
		t.Fatal(err)
	}
}

func TestPersistentReceiverRejectsUnsafeState(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "state")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	id := receiverKeyID(testKey, "uplink")
	path := filepath.Join(dir, id+".slot")
	if err := os.WriteFile(path, []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	if r, err := NewPersistentReceiver(testKey, "uplink", time.Second, dir); err == nil {
		r.Close()
		t.Fatal("corrupt state accepted")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := ClaimReceiverKey(dir, testKey, "uplink"); err != nil {
		t.Fatal(err)
	}
	if r, err := NewPersistentReceiver(testKey, "uplink", time.Second, dir); err == nil {
		r.Close()
		t.Fatal("legacy key reused")
	}
}
