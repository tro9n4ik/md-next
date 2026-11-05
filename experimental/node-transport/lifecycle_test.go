//go:build linux

package transport

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type memoryDevice struct {
	input    chan []byte
	closed   chan struct{}
	once     sync.Once
	received atomic.Int64
	seen     sync.Map
	anomaly  bool
}

func newMemoryDevice() *memoryDevice {
	return &memoryDevice{input: make(chan []byte, 256), closed: make(chan struct{})}
}
func (d *memoryDevice) BatchSize() int { return 32 }
func (d *memoryDevice) Close() error   { d.once.Do(func() { close(d.closed) }); return nil }
func (d *memoryDevice) ReadPackets(buffers [][]byte, sizes []int) (int, error) {
	if d.anomaly {
		d.anomaly = false
		for i, n := range []int{-1, 0, 1500, 9000, 70000} {
			sizes[i] = n
		}
		return 5, nil
	}
	select {
	case <-d.closed:
		return 0, io.EOF
	case packet := <-d.input:
		sizes[0] = copy(buffers[0], packet)
		return 1, nil
	}
}
func (d *memoryDevice) WritePackets(buffers [][]byte, offset int) error {
	for _, b := range buffers {
		if len(b) < offset+24 {
			return errors.New("short delivered frame")
		}
		id := binary.BigEndian.Uint32(b[offset+20:])
		if _, loaded := d.seen.LoadOrStore(id, true); !loaded {
			d.received.Add(1)
		}
	}
	return nil
}
func openTestSocket(a *net.UDPAddr) (*net.UDPConn, error) {
	c, e := net.ListenUDP("udp4", a)
	if e == nil {
		c.SetReadBuffer(4 << 20)
		c.SetWriteBuffer(4 << 20)
	}
	return c, e
}
func testConfig(t *testing.T, bind, peer, direction string) TunnelConfig {
	t.Helper()
	state := t.TempDir()
	if err := os.Chmod(state, 0700); err != nil {
		t.Fatal(err)
	}
	return TunnelConfig{Bind: net.ParseIP(bind), Peer: net.ParseIP(peer), Direction: direction, Base: 42000, Ports: 32,
		Interval: 20 * time.Second, Grace: 15 * time.Second, Key: testKey, State: state}
}

func TestRapidRotationTenThousandFramesAndFDs(t *testing.T) {
	initialFDs, _ := os.ReadDir("/proc/self/fd")
	start := time.Now()
	clock := func() time.Time { return start.Add(time.Since(start) * 200) }
	opts := loopOptions{now: clock, tick: 5 * time.Millisecond, open: openTestSocket}
	a, b := newMemoryDevice(), newMemoryDevice()
	ctx, cancel := context.WithCancel(context.Background())
	results := make(chan error, 2)
	cfgA, cfgB := testConfig(t, "127.0.0.1", "127.0.0.2", "uplink"), testConfig(t, "127.0.0.2", "127.0.0.1", "downlink")
	go func() { results <- runTunnel(ctx, a, cfgA, opts) }()
	go func() { results <- runTunnel(ctx, b, cfgB, opts) }()
	defer func() {
		cancel()
		for i := 0; i < 2; i++ {
			select {
			case e := <-results:
				if e != nil {
					t.Error(e)
				}
			case <-time.After(3 * time.Second):
				t.Error("shutdown did not join workers")
			}
		}
	}()
	time.Sleep(50 * time.Millisecond)
	samples := []int{}
	// Fifty real 100ms slots, 200 frames per slot; no production timing override is exported.
	for cycle := 0; cycle < 50; cycle++ {
		for i := 0; i < 200; i++ {
			packet := make([]byte, 24)
			packet[0] = 0x45
			binary.BigEndian.PutUint16(packet[2:], 24)
			binary.BigEndian.PutUint32(packet[20:], uint32(cycle*200+i))
			select {
			case a.input <- packet:
			case <-time.After(time.Second):
				t.Fatal("sender stalled")
			}
		}
		time.Sleep(100 * time.Millisecond)
		descriptors, _ := os.ReadDir("/proc/self/fd")
		samples = append(samples, len(descriptors))
		if len(descriptors) > len(initialFDs)+16 {
			t.Fatalf("FD leak: initial %d current %d", len(initialFDs), len(descriptors))
		}
	}
	deadline := time.Now().Add(2 * time.Second)
	for b.received.Load() < 10000 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if b.received.Load() != 10000 {
		t.Fatalf("delivered %d/10000", b.received.Load())
	}
	t.Logf("50 rotations: FD first=%d last=%d; delivered=%d", samples[0], samples[len(samples)-1], b.received.Load())
}

func TestAnomalousTUNLengthsDoNotPanic(t *testing.T) {
	device := newMemoryDevice()
	device.anomaly = true
	stats := new(TunnelStats)
	cfg := testConfig(t, "127.0.0.1", "127.0.0.2", "uplink")
	cfg.Stats = stats
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() {
		result <- runTunnel(ctx, device, cfg, loopOptions{now: time.Now, tick: time.Millisecond, open: openTestSocket})
	}()
	deadline := time.Now().Add(time.Second)
	for stats.InvalidFrames.Load() < 5 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	cancel()
	select {
	case err := <-result:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("shutdown stalled")
	}
	if stats.InvalidFrames.Load() != 5 {
		t.Fatal("anomalous frame was not counted", stats.InvalidFrames.Load())
	}
}
