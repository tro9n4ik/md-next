package transport

import (
	"bytes"
	"encoding/binary"
	"errors"
	"math"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"
)

var testKey = bytes.Repeat([]byte{0x17}, 32)
var testNow = time.Unix(1800000000, 0)

func pair(t testing.TB) (*Sender, *Receiver) {
	t.Helper()
	s, e := NewSender(testKey, "uplink", 25*time.Second)
	if e != nil {
		t.Fatal(e)
	}
	r, e := NewReceiver(testKey, "uplink", 25*time.Second)
	if e != nil {
		t.Fatal(e)
	}
	return s, r
}
func packet(t testing.TB, s *Sender, data string, now time.Time) []byte {
	t.Helper()
	p, e := s.Seal([]byte(data), now)
	if e != nil {
		t.Fatal(e)
	}
	return p
}

func TestRoundTripAndPadding(t *testing.T) {
	s, r := pair(t)
	sizes := map[int]bool{}
	for i := 0; i < 100; i++ {
		p := packet(t, s, "message", testNow)
		sizes[len(p)] = true
		if p[0] != 0x43 || len(p) < HeaderSize+16+18 || len(p) > MaxPacket {
			t.Fatal("invalid layout")
		}
		data, e := r.Open(p, testNow)
		if e != nil || string(data) != "message" {
			t.Fatal("roundtrip", e)
		}
	}
	if len(sizes) < 2 {
		t.Fatal("padding does not vary")
	}
}

func TestAuthenticationAndReplay(t *testing.T) {
	s, r := pair(t)
	p := packet(t, s, "secret", testNow)
	for _, index := range []int{0, 1, 9, 13, HeaderSize, len(p) - 1} {
		changed := bytes.Clone(p)
		changed[index] ^= 1
		if _, e := r.Open(changed, testNow); !errors.Is(e, ErrPacket) {
			t.Fatal("tampering accepted", index)
		}
	}
	if len(r.windows) != 0 {
		t.Fatal("unauthenticated input allocated session state")
	}
	if _, e := r.Open(p, testNow); e != nil {
		t.Fatal(e)
	}
	if _, e := r.Open(p, testNow); e == nil {
		t.Fatal("replay accepted")
	}
	other, _ := NewReceiver(testKey, "downlink", 25*time.Second)
	if _, e := other.Open(p, testNow); e == nil {
		t.Fatal("reflection accepted")
	}
	wrong, _ := NewReceiver(bytes.Repeat([]byte{1}, 32), "uplink", 25*time.Second)
	if _, e := wrong.Open(p, testNow); e == nil {
		t.Fatal("wrong key accepted")
	}
}

func TestReorderingAndWindow(t *testing.T) {
	s, r := pair(t)
	packets := make([][]byte, 70)
	for i := range packets {
		packets[i] = packet(t, s, "x", testNow)
	}
	for _, i := range []int{69, 68, 7, 6} {
		if _, e := r.Open(packets[i], testNow); e != nil {
			t.Fatal(i, e)
		}
	}
	if _, e := r.Open(packets[0], testNow); e == nil {
		t.Fatal("old packet accepted")
	}
}

func TestClockSkewAndRollback(t *testing.T) {
	s, r := pair(t)
	p := packet(t, s, "x", testNow)
	if _, e := r.Open(p, testNow.Add(25*time.Second)); e != nil {
		t.Fatal("one slot skew", e)
	}
	if _, e := r.Open(p, testNow.Add(75*time.Second)); e == nil {
		t.Fatal("expired packet")
	}
	if _, e := r.Open(p, testNow); e == nil {
		t.Fatal("clock rollback reopened old slot")
	}
}

func TestCounterRolloverAndSenderRestart(t *testing.T) {
	s, r := pair(t)
	s.sequence = math.MaxUint32
	first := packet(t, s, "last", testNow)
	second := packet(t, s, "new", testNow)
	if binary.BigEndian.Uint32(first[9:13]) != math.MaxUint32 || binary.BigEndian.Uint32(second[9:13]) != 0 || bytes.Equal(first[13:29], second[13:29]) {
		t.Fatal("nonce/session reused")
	}
	if _, e := r.Open(first, testNow); e != nil {
		t.Fatal(e)
	}
	if _, e := r.Open(second, testNow); e != nil {
		t.Fatal(e)
	}
	restarted, _ := NewSender(testKey, "uplink", 25*time.Second)
	third := packet(t, restarted, "restart", testNow)
	if bytes.Equal(third[13:29], second[13:29]) {
		t.Fatal("session salt reused")
	}
	if _, e := r.Open(third, testNow); e != nil {
		t.Fatal(e)
	}
}

func TestReceiverRestartRequiresNewSecret(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "private")
	if e := ClaimReceiverKey(dir, testKey, "uplink"); e != nil {
		t.Fatal(e)
	}
	if e := ClaimReceiverKey(dir, testKey, "uplink"); e == nil {
		t.Fatal("receiver restart reused key")
	}
	key := bytes.Repeat([]byte{9}, 32)
	if e := ClaimReceiverKey(dir, key, "uplink"); e != nil {
		t.Fatal(e)
	}
	s, _ := pair(t)
	newReceiver, _ := NewReceiver(key, "uplink", 25*time.Second)
	if _, e := newReceiver.Open(packet(t, s, "old", testNow), testNow); e == nil {
		t.Fatal("old receiver packet accepted")
	}
}

func TestBoundsAndSessionLimit(t *testing.T) {
	s, r := pair(t)
	if _, e := s.Seal(make([]byte, MaxPayload+1), testNow); e == nil {
		t.Fatal("oversized payload")
	}
	for _, bad := range [][]byte{nil, {0x43}, make([]byte, MaxPacket+1)} {
		if _, e := r.Open(bad, testNow); e == nil {
			t.Fatal("bad size")
		}
	}
	for i := 0; i < MaxSessions; i++ {
		sender, _ := NewSender(testKey, "uplink", 25*time.Second)
		if _, e := r.Open(packet(t, sender, "x", testNow), testNow); e != nil {
			t.Fatal(e)
		}
	}
	if _, e := r.Open(packet(t, s, "overflow", testNow), testNow); e == nil {
		t.Fatal("unbounded sessions")
	}
}

func TestConcurrentSender(t *testing.T) {
	s, r := pair(t)
	packets := make(chan []byte, 32)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			p, e := s.Seal([]byte("x"), testNow)
			if e == nil {
				packets <- p
			}
		}()
	}
	wg.Wait()
	close(packets)
	count := 0
	for p := range packets {
		if _, e := r.Open(p, testNow); e != nil {
			t.Fatal(e)
		}
		count++
	}
	if count != 32 {
		t.Fatal(count)
	}
}

func TestPortHopping(t *testing.T) {
	s, _ := pair(t)
	ports := map[int]bool{}
	for i := 0; i < 20; i++ {
		at := testNow.Add(time.Duration(i) * 25 * time.Second)
		p, e := s.Port(at, 38010, 4)
		if e != nil || p < 38010 || p > 38013 {
			t.Fatal(p, e)
		}
		again, _ := s.Port(at.Add(time.Millisecond), 38010, 4)
		if again != p {
			t.Fatal("not deterministic")
		}
		ports[p] = true
	}
	if len(ports) < 2 {
		t.Fatal("ports did not rotate")
	}
	if _, e := s.Port(testNow, 65535, 2); e == nil {
		t.Fatal("overflowing range")
	}
}

func TestUDPLoopback(t *testing.T) {
	server, e := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if e != nil {
		t.Fatal(e)
	}
	defer server.Close()
	client, e := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if e != nil {
		t.Fatal(e)
	}
	defer client.Close()
	s, r := pair(t)
	p := packet(t, s, "loopback", testNow)
	client.WriteToUDP(p, server.LocalAddr().(*net.UDPAddr))
	server.SetReadDeadline(time.Now().Add(time.Second))
	buf := make([]byte, MaxPacket+1)
	n, _, e := server.ReadFromUDP(buf)
	if e != nil {
		t.Fatal(e)
	}
	data, e := r.Open(buf[:n], testNow)
	if e != nil || string(data) != "loopback" {
		t.Fatal(e)
	}
}

func TestIsolatedTUN(t *testing.T) {
	if os.Getenv("MDNEXT_ISOLATED_NETNS") != "1" || runtime.GOOS != "linux" {
		t.Skip("requires explicitly isolated Linux network namespace")
	}
	name := "mdntest0"
	f, e := OpenTUN(name)
	if e != nil {
		t.Fatal(e)
	}
	if output, e := exec.Command("ip", "link", "set", name, "mtu", "1024", "up").CombinedOutput(); e != nil {
		f.Close()
		t.Fatal(e, string(output))
	}
	f.Close()
	if exec.Command("ip", "link", "show", name).Run() == nil {
		t.Fatal("TUN device survived closing descriptor")
	}
}

func FuzzOpen(f *testing.F) {
	s, _ := NewSender(testKey, "uplink", 25*time.Second)
	p, _ := s.Seal([]byte("seed"), testNow)
	f.Add(p)
	f.Add([]byte{0x43})
	f.Fuzz(func(t *testing.T, input []byte) {
		r, _ := NewReceiver(testKey, "uplink", 25*time.Second)
		out, e := r.Open(input, testNow)
		if e == nil && len(out) > MaxPayload {
			t.Fatal("oversized output")
		}
	})
}

func BenchmarkSeal(b *testing.B) {
	s, _ := pair(b)
	payload := make([]byte, MaxPayload)
	b.SetBytes(MaxPayload)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if _, e := s.Seal(payload, testNow); e != nil {
			b.Fatal(e)
		}
	}
}
func BenchmarkRoundTrip(b *testing.B) {
	s, r := pair(b)
	payload := make([]byte, MaxPayload)
	b.SetBytes(MaxPayload)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		p, e := s.Seal(payload, testNow)
		if e != nil {
			b.Fatal(e)
		}
		if _, e = r.Open(p, testNow); e != nil {
			b.Fatal(e)
		}
	}
}

func TestSealIntoHonorsWireMTU(t *testing.T) {
	sender, receiver := pair(t)
	var encoded, scratch, decoded [2048]byte
	payload := make([]byte, MaxPayload)
	for i := 0; i < 100; i++ {
		packet, err := sender.SealInto(encoded[:0], scratch[:], payload, testNow)
		if err != nil || len(packet) > MaxPacket {
			t.Fatalf("wire MTU: %d %v", len(packet), err)
		}
		result, err := receiver.OpenInto(decoded[:0], packet, testNow)
		if err != nil || len(result) != MaxPayload {
			t.Fatal(err)
		}
	}
}

func BenchmarkReuseRoundTrip(b *testing.B) {
	sender, receiver := pair(b)
	var encoded, scratch, decoded [2048]byte
	payload := make([]byte, MaxPayload)
	b.SetBytes(MaxPayload)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		packet, err := sender.SealInto(encoded[:0], scratch[:], payload, testNow)
		if err != nil {
			b.Fatal(err)
		}
		if _, err = receiver.OpenInto(decoded[:0], packet, testNow); err != nil {
			b.Fatal(err)
		}
	}
}
