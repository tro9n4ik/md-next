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
	rawTUN, err := device.SyscallConn()
	if err != nil {
		return err
	}
	start(func() {
		connection := ipv4.NewPacketConn(egress)
		var messages [batchSize]ipv4.Message
		var buffers [batchSize][1][]byte
		var items [batchSize]*frame
		var addresses [batchSize]net.UDPAddr
		var scratch [2048]byte
		portSlot := int64(-1)
		port := 0
		for i := range messages {
			items[i] = framePool.Get().(*frame)
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
			count := 0
			var readErr error
			err := rawTUN.Read(func(fd uintptr) bool {
				for count < batchSize {
					n, e := syscall.Read(int(fd), items[count].plain[:])
					if e == syscall.EINTR {
						continue
					}
					if e == syscall.EAGAIN || e == syscall.EWOULDBLOCK {
						return count > 0
					}
					if e != nil {
						readErr = e
						return true
					}
					if n == 0 {
						readErr = errors.New("TUN returned EOF")
						return true
					}
					items[count].size = n
					count++
				}
				return true
			})
			if err != nil {
				report(err)
				return
			}
			if readErr != nil {
				report(readErr)
				return
			}
			now := time.Now()
			slot := now.Unix() / int64(config.Interval/time.Second)
			if slot != portSlot {
				port, err = sender.Port(now, config.Base, config.Ports)
				if err != nil {
					report(err)
					return
				}
				portSlot = slot
			}
			valid := 0
			for i := 0; i < count; i++ {
				item := items[i]
				if !ValidIP(item.plain[:item.size]) {
					continue
				}
				packet, e := sender.SealInto(item.packet[:0], scratch[:], item.plain[:item.size], now)
				if e != nil {
					report(e)
					return
				}
				buffers[valid][0] = packet
				addresses[valid].Port = port
				valid++
			}
			egress.SetWriteDeadline(time.Now().Add(time.Second))
			for sent := 0; sent < valid; {
				n, e := connection.WriteBatch(messages[sent:valid], 0)
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
