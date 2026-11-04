//go:build linux

package main

import (
	"context"
	"encoding/hex"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"runtime/pprof"
	"strings"
	"syscall"
	"time"

	transport "mdnext.local/node-transport"
)

func run() error {
	deviceName := flag.String("tun", "mdnt0", "preconfigured private TUN name")
	bind := flag.String("bind", "", "local IPv4 address")
	peer := flag.String("peer", "", "permitted peer IPv4 address")
	direction := flag.String("direction", "", "uplink or downlink")
	base := flag.Int("base", 39000, "first port")
	ports := flag.Int("ports", 32, "number of ports")
	keyFile := flag.String("key-file", "", "private file with a 32-byte hexadecimal key")
	state := flag.String("state", "", "private persistent receiver key-use directory")
	profile := flag.String("cpu-profile", "", "optional private CPU profile for an isolated benchmark")
	flag.Parse()
	if *profile != "" {
		file, err := os.OpenFile(*profile, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		defer file.Close()
		if err = pprof.StartCPUProfile(file); err != nil {
			return err
		}
		defer pprof.StopCPUProfile()
	}
	if *state == "" {
		return fmt.Errorf("state directory required")
	}
	info, err := os.Lstat(*keyFile)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 128 {
		return fmt.Errorf("key file must be private, regular and at most 128 bytes")
	}
	encoded, err := os.ReadFile(*keyFile)
	if err != nil {
		return err
	}
	key, err := hex.DecodeString(strings.TrimSpace(string(encoded)))
	if err != nil || len(key) != 32 {
		return fmt.Errorf("invalid key")
	}
	device, err := transport.OpenTUN(*deviceName)
	if err != nil {
		return err
	}
	defer device.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	fmt.Println("TUN открыт, ожидается настройка адреса и MTU 1380 средствами стенда")
	ready := time.NewTimer(30 * time.Second)
	defer ready.Stop()
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
waitForInterface:
	for {
		iface, lookupErr := net.InterfaceByName(*deviceName)
		if lookupErr == nil && iface.Flags&net.FlagUp != 0 && iface.MTU == transport.MaxPayload {
			break waitForInterface
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ready.C:
			return fmt.Errorf("TUN не подготовлен: требуются состояние UP и MTU %d", transport.MaxPayload)
		case <-tick.C:
		}
	}
	return transport.RunTunnel(ctx, device, transport.TunnelConfig{
		Bind: net.ParseIP(*bind), Peer: net.ParseIP(*peer), Direction: *direction,
		Base: *base, Ports: *ports, Interval: 20 * time.Second, Grace: 15 * time.Second,
		Key: key, State: *state,
	})
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
