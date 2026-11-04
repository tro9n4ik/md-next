//go:build linux

package transport

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"os"
	"sync"
	"syscall"
	"time"

	"golang.org/x/net/ipv4"
)

const batchSize = 32

type frame struct {
	packet [2048]byte
	plain  [2048]byte
	size   int
}

var framePool = sync.Pool{New: func() any { return new(frame) }}

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
func RunTunnel(ctx context.Context, device *os.File, config TunnelConfig) error {
	if config.Bind.To4() == nil || config.Peer.To4() == nil || config.Interval < 20*time.Second ||
		config.Interval > 30*time.Second || config.Grace < 10*time.Second || config.Grace > 15*time.Second ||
		config.Ports < 2 || config.Ports > 128 || config.Base < 1024 || config.Base+config.Ports > 65536 {
		return errors.New("invalid IPv4 peer, port range, interval or grace period")
	}
	sender, err := NewSender(config.Key, config.Direction, config.Interval)
	if err != nil {
		return err
	}
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
	egress, err := socket(&net.UDPAddr{IP: config.Bind})
	if err != nil {
		return err
	}
	defer egress.Close()
	defer device.Close()
	outbound := make(chan *frame, 256)
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
		defer close(outbound)
		var scratch [2048]byte
		for ctx.Err() == nil {
			item := framePool.Get().(*frame)
			n, err := device.Read(item.plain[:])
			if err != nil {
				framePool.Put(item)
				report(err)
				return
			}
			if !ValidIP(item.plain[:n]) {
				framePool.Put(item)
				continue
			}
			packet, err := sender.SealInto(item.packet[:0], scratch[:], item.plain[:n], time.Now())
			if err != nil {
				framePool.Put(item)
				report(err)
				return
			}
			item.size = len(packet)
			select {
			case outbound <- item:
			case <-ctx.Done():
				framePool.Put(item)
				return
			}
		}
	})
	start(func() {
		connection := ipv4.NewPacketConn(egress)
		var messages [batchSize]ipv4.Message
		var buffers [batchSize][1][]byte
		var items [batchSize]*frame
		var addresses [batchSize]net.UDPAddr
		for i := range messages {
			messages[i].Buffers = buffers[i][:]
			messages[i].Addr = &addresses[i]
			addresses[i].IP = config.Peer
		}
		for item := range outbound {
			items[0] = item
			count := 1
		gather:
			for count < batchSize {
				select {
				case next, ok := <-outbound:
					if !ok {
						break gather
					}
					items[count] = next
					count++
				default:
					break gather
				}
			}
			port, err := sender.Port(time.Now(), config.Base, config.Ports)
			if err == nil {
				for i := 0; i < count; i++ {
					buffers[i][0] = items[i].packet[:items[i].size]
					addresses[i].Port = port
				}
				egress.SetWriteDeadline(time.Now().Add(time.Second))
				for sent := 0; sent < count && err == nil; {
					var n int
					n, err = connection.WriteBatch(messages[sent:count], 0)
					if n == 0 && err == nil {
						err = errors.New("UDP batch made no progress")
					}
					sent += n
				}
			}
			for i := 0; i < count; i++ {
				framePool.Put(items[i])
				items[i] = nil
			}
			if err != nil && ctx.Err() == nil {
				report(err)
			}
		}
	})

	type listener struct {
		conn    *net.UDPConn
		expires time.Time
	}
	listeners := map[int]listener{}
	listen := func(port int) error {
		if _, ok := listeners[port]; ok {
			return nil
		}
		conn, err := socket(&net.UDPAddr{IP: config.Bind, Port: port})
		if err != nil {
			return err
		}
		listeners[port] = listener{conn: conn}
		start(func() {
			packetConn := ipv4.NewPacketConn(conn)
			var messages [batchSize]ipv4.Message
			var buffers [batchSize][1][]byte
			var items [batchSize]*frame
			for i := range messages {
				items[i] = framePool.Get().(*frame)
				buffers[i][0] = items[i].packet[:MaxPacket+1]
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
					if ctx.Err() == nil && !errors.Is(err, net.ErrClosed) {
						report(err)
					}
					return
				}
				for i := 0; i < n; i++ {
					peer, ok := messages[i].Addr.(*net.UDPAddr)
					if !ok || !peer.IP.Equal(config.Peer) {
						continue
					}
					packet, err := receiver.OpenInto(items[i].plain[:0], items[i].packet[:messages[i].N], time.Now())
					if err != nil || !ValidIP(packet) {
						continue
					}
					if _, err = device.Write(packet); err != nil {
						report(err)
						return
					}
				}
			}
		})
		return nil
	}
	rotate := func(now time.Time) error {
		active, _ := receiver.Port(now, config.Base, config.Ports)
		next, _ := receiver.Port(now.Add(config.Interval), config.Base, config.Ports)
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
				state.conn.Close()
				delete(listeners, port)
			}
		}
		return nil
	}
	if err = rotate(time.Now()); err != nil {
		report(err)
	}
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for ctx.Err() == nil {
		select {
		case <-ctx.Done():
		case now := <-ticker.C:
			if err := rotate(now); err != nil {
				report(err)
			}
		}
	}
	for _, state := range listeners {
		state.conn.Close()
	}
	device.Close()
	egress.Close()
	workers.Wait()
	select {
	case err := <-failures:
		return err
	default:
		return nil
	}
}
