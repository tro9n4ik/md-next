//go:build linux

package transport

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"golang.org/x/net/ipv4"
)

const batchSize = 32
const sendPacketCapacity = 128

type frame struct {
	packet [2048]byte
	plain  [65535 + 32]byte
	size   int
}

var framePool = sync.Pool{New: func() any { return new(frame) }}

type TunnelStats struct {
	InvalidFrames atomic.Uint64
}

type loopOptions struct {
	now  func() time.Time
	tick time.Duration
	open func(*net.UDPAddr) (*net.UDPConn, error)
}

type TunnelConfig struct {
	Bind      net.IP
	Peer      net.IP
	Base      int
	Ports     int
	Interval  time.Duration
	Grace     time.Duration
	State     string
	Key       []byte
	Direction string
	UDPGSO    bool
	Stats     *TunnelStats
}

// ValidIP rejects incomplete frames before writing to TUN.
func ValidIP(packet []byte) bool {
	if len(packet) > MaxPayload || len(packet) < 20 {
		return false
	}
	switch packet[0] >> 4 {
	case 4:
		header := int(packet[0]&15) * 4
		return header >= 20 && len(packet) >= header && int(binary.BigEndian.Uint16(packet[2:4])) == len(packet)
	case 6:
		return len(packet) >= 40 && int(binary.BigEndian.Uint16(packet[4:6]))+40 == len(packet)
	}
	return false
}

func socket(address *net.UDPAddr) (*net.UDPConn, error) {
	conn, err := net.ListenUDP("udp4", address)
	if err != nil {
		return nil, err
	}
	if err = conn.SetReadBuffer(8 << 20); err == nil {
		err = conn.SetWriteBuffer(8 << 20)
	}
	if err != nil {
		conn.Close()
		return nil, err
	}
	raw, err := conn.SyscallConn()
	if err == nil {
		controlErr := raw.Control(func(fd uintptr) {
			for _, option := range []int{syscall.SO_RCVBUF, syscall.SO_SNDBUF} {
				var size int
				size, err = syscall.GetsockoptInt(int(fd), syscall.SOL_SOCKET, option)
				if err == nil && size < 16<<20 {
					err = fmt.Errorf("UDP buffer capped by kernel: raise net.core.rmem_max and net.core.wmem_max to 8388608")
				}
				if err != nil {
					return
				}
			}
		})
		if controlErr != nil {
			err = controlErr
		}
	}
	if err != nil {
		conn.Close()
		return nil, err
	}
	return conn, nil
}

// RunTunnel owns the TUN descriptor until shutdown. Routing is configured by
// the isolated test harness, never implicitly by the daemon.
func RunTunnel(ctx context.Context, device PacketDevice, config TunnelConfig) error {
	return runTunnel(ctx, device, config, loopOptions{now: time.Now, tick: 100 * time.Millisecond, open: socket})
}
func closeUDP(conn *net.UDPConn) {
	_ = conn.SetDeadline(time.Now().Add(-10 * time.Millisecond))
	_ = conn.Close()
}
func runTunnel(ctx context.Context, device PacketDevice, config TunnelConfig, options loopOptions) error {
	if device == nil {
		return errors.New("nil TUN device")
	}
	defer device.Close()
	stats := config.Stats
	if stats == nil {
		stats = new(TunnelStats)
	}

	if config.Bind.To4() == nil || config.Peer.To4() == nil || config.Interval < 20*time.Second ||
		config.Interval > 30*time.Second || config.Grace < 10*time.Second || config.Grace > 15*time.Second ||
		device.BatchSize() < 1 || device.BatchSize() > sendPacketCapacity || config.Ports < 2 || config.Ports > 128 || config.Base < 1024 || config.Base+config.Ports > 65536 {
		return errors.New("invalid IPv4 peer, port range, interval or grace period")
	}
	sender, err := NewSender(config.Key, config.Direction, config.Interval)
	if err != nil {
		return err
	}
	sender.segmentPadding = config.UDPGSO
	direction := "uplink"
	if config.Direction == direction {
		direction = "downlink"
	}
	receiver, err := NewReceiver(config.Key, direction, config.Interval)
	if err != nil {
		return err
	}
	if err = ClaimReceiverKey(config.State, config.Key, direction); err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var egress *net.UDPConn
	for attempt := 0; attempt < 16; attempt++ {
		egress, err = options.open(&net.UDPAddr{IP: config.Bind})
		if err != nil {
			return err
		}
		port := egress.LocalAddr().(*net.UDPAddr).Port
		if port < config.Base || port >= config.Base+config.Ports {
			break
		}
		closeUDP(egress)
		egress = nil
	}
	if egress == nil {
		return errors.New("cannot reserve non-hopping source port")
	}
	defer closeUDP(egress)
	failures := make(chan error, 1)
	report := func(err error) {
		if ctx.Err() != nil {
			return
		}
		select {
		case failures <- err:
		default:
		}
		cancel()
	}
	var workers sync.WaitGroup
	start := func(fn func()) {
		workers.Add(1)
		go func() { defer workers.Done(); fn() }()
	}
	start(func() {
		connection := ipv4.NewPacketConn(egress)
		var messages [sendPacketCapacity]ipv4.Message
		var buffers [sendPacketCapacity][1][]byte
		items := make([]*frame, device.BatchSize())
		readBuffers := make([][]byte, len(items))
		sizes := make([]int, len(items))
		var addresses [sendPacketCapacity]net.UDPAddr
		var scratch [2048]byte
		var segments segmentBatch
		portSlot := int64(-1)
		port := 0
		var deadlineRefresh time.Time
		for i := range items {
			items[i] = framePool.Get().(*frame)
			readBuffers[i] = items[i].plain[:65536]
		}
		for i := range messages {
			messages[i].Buffers = buffers[i][:]
			messages[i].Addr = &addresses[i]
			addresses[i].IP = config.Peer
		}
		defer func() {
			for _, item := range items {
				framePool.Put(item)
			}
		}()
		for ctx.Err() == nil {
			count, err := device.ReadPackets(readBuffers, sizes)
			if err != nil {
				report(err)
				return
			}

			if count < 0 || count > len(items) {
				report(errors.New("invalid TUN batch count"))
				return
			}
			now := options.now()
			slot := now.Unix() / int64(config.Interval/time.Second)
			if slot != portSlot {
				port, err = sender.Port(now, config.Base, config.Ports)
				if err != nil {
					report(err)
					return
				}
				portSlot = slot
			}
			burst := batchSize
			if config.UDPGSO {
				burst = sendPacketCapacity
			}
			for offset := 0; offset < count; offset += burst {
				end := min(offset+burst, count)
				valid := 0
				for i := offset; i < end; i++ {
					item := items[i]
					if sizes[i] <= 0 || sizes[i] > len(readBuffers[i]) {
						stats.InvalidFrames.Add(1)
						continue
					}
					if !ValidIP(item.plain[:sizes[i]]) {
						stats.InvalidFrames.Add(1)
						continue
					}
					// This goroutine exclusively owns the Sender's mutable state.
					packet, e := sender.sealOwned(item.packet[:0], scratch[:], item.plain[:sizes[i]], now)
					if e != nil {
						report(e)
						return
					}
					buffers[valid][0] = packet
					addresses[valid].Port = port
					valid++
				}
				if !now.Before(deadlineRefresh) {
					if e := egress.SetWriteDeadline(time.Now().Add(time.Second)); e != nil {
						report(e)
						return
					}
					deadlineRefresh = now.Add(250 * time.Millisecond)
				}
				toSend := messages[:valid]
				if config.UDPGSO {
					toSend, err = segments.pack(toSend)
					if err != nil {
						report(err)
						return
					}
				}
				for sent := 0; sent < len(toSend); {
					n, e := connection.WriteBatch(toSend[sent:min(sent+batchSize, len(toSend))], 0)
					if e != nil {
						report(e)
						return
					}
					if n == 0 {
						report(errors.New("UDP batch made no progress"))
						return
					}
					sent += n
				}
			}
		}
	})

	type listener struct {
		conn    *net.UDPConn
		expires time.Time
		retired atomic.Bool
	}
	// Single-owner map; reader goroutines never access it.
	listeners := map[int]*listener{}
	listen := func(port int) error {
		if _, ok := listeners[port]; ok {
			return nil
		}
		conn, err := options.open(&net.UDPAddr{IP: config.Bind, Port: port})
		if err != nil {
			return err
		}
		if config.UDPGSO {
			if err := enableGRO(conn); err != nil {
				conn.Close()
				return err
			}
		}
		state := &listener{conn: conn}
		listeners[port] = state
		start(func() {
			packetConn := ipv4.NewPacketConn(conn)
			var messages [batchSize]ipv4.Message
			var buffers [batchSize][1][]byte
			var items [batchSize]*frame
			var toTUN [batchSize][]byte
			var control [batchSize][64]byte
			for i := range messages {
				items[i] = framePool.Get().(*frame)
				buffers[i][0] = items[i].packet[:MaxPacket+1]
				if config.UDPGSO {
					buffers[i][0] = make([]byte, 65535)
					messages[i].OOB = control[i][:]
				}
				messages[i].Buffers = buffers[i][:]
			}
			defer func() {
				for _, item := range items {
					framePool.Put(item)
				}
			}()
			for ctx.Err() == nil {
				n, err := packetConn.ReadBatch(messages[:], 0)
				if err != nil {
					if ctx.Err() == nil && !state.retired.Load() && !errors.Is(err, net.ErrClosed) {
						report(err)
					}
					return
				}
				valid := 0
				flush := func() error {
					if valid == 0 {
						return nil
					}
					err := device.WritePackets(toTUN[:valid], 16)
					valid = 0
					return err
				}
				now := options.now()
				for i := 0; i < n; i++ {
					message := &messages[i]
					peer, ok := message.Addr.(*net.UDPAddr)
					if !ok || !peer.IP.Equal(config.Peer) {
						continue
					}
					size, e := datagramSize(message)
					if e != nil {
						continue
					}
					for offset := 0; offset < message.N; offset += size {
						end := min(offset+size, message.N)
						packet, e := receiver.OpenInto(items[valid].plain[14:14], buffers[i][0][offset:end], now)
						if e != nil || !ValidIP(packet) {
							continue
						}
						toTUN[valid] = items[valid].plain[:16+len(packet)]
						valid++
						if valid == batchSize {
							if e := flush(); e != nil {
								report(e)
								return
							}
						}
					}
				}
				if e := flush(); e != nil {
					report(e)
					return
				}
			}
		})
		return nil
	}
	rotateSlot := int64(-1)
	active, next := 0, 0
	rotate := func(now time.Time) error {
		slot := now.Unix() / int64(config.Interval/time.Second)
		if slot != rotateSlot {
			var err error
			active, err = receiver.Port(now, config.Base, config.Ports)
			if err != nil {
				return err
			}
			next, err = receiver.Port(now.Add(config.Interval), config.Base, config.Ports)
			if err != nil {
				return err
			}
			rotateSlot = slot
		}
		for _, port := range []int{active, next} {
			if err := listen(port); err != nil {
				return fmt.Errorf("listen port %d: %w", port, err)
			}
			state := listeners[port]
			state.expires = time.Time{}
			listeners[port] = state
		}
		for port, state := range listeners {
			if port == active || port == next {
				continue
			}
			if state.expires.IsZero() {
				state.expires = now.Add(config.Grace)
				listeners[port] = state
			} else if !now.Before(state.expires) {
				state.retired.Store(true)
				closeUDP(state.conn)
				delete(listeners, port)
			}
		}
		return nil
	}
	if err = rotate(options.now()); err != nil {
		report(err)
	}
	ticker := time.NewTicker(options.tick)
	defer ticker.Stop()
	for ctx.Err() == nil {
		select {
		case <-ctx.Done():
		case <-ticker.C:
			if err := rotate(options.now()); err != nil {
				report(err)
			}
		}
	}
	for _, state := range listeners {
		state.retired.Store(true)
		closeUDP(state.conn)
	}
	device.Close()
	closeUDP(egress)
	workers.Wait()
	select {
	case err := <-failures:
		return err
	default:
		return nil
	}
}
