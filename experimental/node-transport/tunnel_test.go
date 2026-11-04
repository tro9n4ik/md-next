//go:build linux

package transport

import (
	"encoding/binary"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestIPValidation(t *testing.T) {
	packet := make([]byte, 100)
	packet[0] = 0x45
	binary.BigEndian.PutUint16(packet[2:4], 100)
	if !ValidIP(packet) {
		t.Fatal("valid IPv4 refused")
	}
	packet[0] = 0x4f
	if ValidIP(packet[:40]) {
		t.Fatal("truncated IPv4 accepted")
	}
	packet[0] = 0x60
	binary.BigEndian.PutUint16(packet[4:6], 60)
	if !ValidIP(packet) {
		t.Fatal("valid IPv6 refused")
	}
	binary.BigEndian.PutUint16(packet[4:6], 70)
	if ValidIP(packet) {
		t.Fatal("truncated IPv6 accepted")
	}
}

func TestIsolatedTUNReadsIPv4(t *testing.T) {
	if os.Getenv("MDNEXT_ISOLATED_NETNS") != "1" {
		t.Skip("isolated namespace required")
	}
	device, err := OpenTUN("mdread0")
	if err != nil {
		t.Fatal(err)
	}
	defer device.Close()
	for _, args := range [][]string{
		{"addr", "add", "172.30.254.1/30", "dev", "mdread0"},
		{"link", "set", "mdread0", "up", "mtu", "1380"},
	} {
		if out, err := exec.Command("ip", args...).CombinedOutput(); err != nil {
			t.Fatal(err, string(out))
		}
	}
	received := make(chan []byte, 1)
	go func() {
		var buf [2048]byte
		for {
			n, err := device.Read(buf[:])
			if err != nil {
				return
			}
			if buf[0]>>4 == 4 {
				received <- append([]byte(nil), buf[:n]...)
				return
			}
		}
	}()
	ping := exec.Command("ping", "-n", "-c", "1", "-W", "1", "172.30.254.2")
	if err := ping.Start(); err != nil {
		t.Fatal(err)
	}
	defer ping.Wait()
	select {
	case packet := <-received:
		if !ValidIP(packet) {
			t.Fatalf("bad TUN frame: length %d first byte %02x", len(packet), packet[0])
		}
	case <-time.After(3 * time.Second):
		t.Fatal("TUN did not produce the IPv4 packet")
	}
}
