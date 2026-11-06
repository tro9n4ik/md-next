// mdnt is a loopback-only protocol experiment. It is not a production daemon.
package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"flag"
	"fmt"
	transport "mdnext.local/node-transport"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func run() error {
	mode := flag.String("mode", "probe", "serve or probe (loopback only)")
	address := flag.String("address", "127.0.0.1:38000", "server/listen address")
	keyFile := flag.String("key-file", "", "private file containing 64 hexadecimal characters")
	state := flag.String("state-dir", "", "private persistent receiver state directory")
	interval := flag.Duration("interval", 25*time.Second, "rotation interval")
	base := flag.Int("base", 38000, "first destination port")
	count := flag.Int("ports", 1, "destination port count")
	attempts := flag.Int("count", 5, "probe packets")
	request := flag.String("request", "echo", "probe request: echo or status")
	flag.Parse()
	if *request != "echo" && *request != "status" {
		return fmt.Errorf("request must be echo or status")
	}
	if *keyFile == "" || *state == "" {
		return fmt.Errorf("key-file and state-dir are required")
	}
	info, err := os.Lstat(*keyFile)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 128 {
		return fmt.Errorf("key file must be regular, private and <=128 bytes")
	}
	encoded, err := os.ReadFile(*keyFile)
	if err != nil {
		return err
	}
	key, err := hex.DecodeString(string(bytes.TrimSpace(encoded)))
	if err != nil || len(key) != 32 {
		return fmt.Errorf("expected 32-byte hexadecimal key")
	}
	addr, err := net.ResolveUDPAddr("udp4", *address)
	if err != nil {
		return err
	}
	if !addr.IP.IsLoopback() {
		return fmt.Errorf("prototype is restricted to loopback")
	}
	up, down := "uplink", "downlink"
	if *mode == "serve" {
		up, down = down, up
	} else if *mode != "probe" {
		return fmt.Errorf("invalid mode")
	}
	sender, err := transport.NewSender(key, up, *interval)
	if err != nil {
		return err
	}
	receiver, err := transport.NewPersistentReceiver(key, down, *interval, *state)
	if err != nil {
		return err
	}
	defer receiver.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	bind := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)}
	if *mode == "serve" {
		bind = addr
	}
	conn, err := net.ListenUDP("udp4", bind)
	if err != nil {
		return err
	}
	defer conn.Close()
	go func() { <-ctx.Done(); conn.Close() }()
	buffer := make([]byte, transport.MaxPacket+1)
	if *mode == "serve" {
		started := time.Now()
		fmt.Println("Experimental loopback receiver ready")
		for {
			n, peer, readErr := conn.ReadFromUDP(buffer)
			if readErr != nil {
				if ctx.Err() != nil {
					return nil
				}
				return readErr
			}
			if !peer.IP.IsLoopback() {
				continue
			}
			payload, openErr := receiver.Open(buffer[:n], time.Now())
			if openErr != nil {
				continue
			}
			if bytes.HasPrefix(payload, []byte(statusMagic)) {
				payload = statusReply(payload, time.Now(), started)
				if payload == nil {
					continue
				}
			}
			packet, sealErr := sender.Seal(payload, time.Now())
			if sealErr != nil {
				return sealErr
			}
			conn.SetWriteDeadline(time.Now().Add(time.Second))
			if _, err = conn.WriteToUDP(packet, peer); err != nil {
				if ctx.Err() != nil {
					return nil
				}
				return err
			}
		}
	}
	if *attempts < 1 || *attempts > 1000 {
		return fmt.Errorf("count must be 1..1000")
	}
	for i := 0; i < *attempts; i++ {
		payload := []byte(fmt.Sprintf("synthetic-probe-%d", i))
		now := time.Now()
		if *request == "status" {
			payload, err = newStatusRequest(now)
			if err != nil {
				return err
			}
		}
		packet, e := sender.Seal(payload, now)
		if e != nil {
			return e
		}
		port, e := sender.Port(now, *base, *count)
		if e != nil {
			return e
		}
		peer := &net.UDPAddr{IP: addr.IP, Port: port}
		conn.SetWriteDeadline(time.Now().Add(time.Second))
		if _, e = conn.WriteToUDP(packet, peer); e != nil {
			if ctx.Err() != nil {
				return nil
			}
			return e
		}
		conn.SetReadDeadline(time.Now().Add(2 * time.Second))
		n, remote, e := conn.ReadFromUDP(buffer)
		if e != nil {
			if ctx.Err() != nil {
				return nil
			}
			return e
		}
		if !remote.IP.Equal(addr.IP) || (*request == "status" && remote.Port != addr.Port && remote.Port != port) {
			return fmt.Errorf("unexpected source")
		}
		decoded, e := receiver.Open(buffer[:n], time.Now())
		if e != nil {
			return fmt.Errorf("probe authentication failed")
		}
		if *request == "status" {
			uptime, err := readStatusReply(payload, decoded, time.Now())
			if err != nil {
				return err
			}
			fmt.Printf("Authenticated status %d via port %d OK uptime_ms=%d\n", i+1, port, uptime)
		} else {
			if !bytes.Equal(decoded, payload) {
				return fmt.Errorf("probe echo payload mismatch")
			}
			fmt.Printf("Authenticated probe %d via port %d OK\n", i+1, port)
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(300 * time.Millisecond):
		}
	}
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
