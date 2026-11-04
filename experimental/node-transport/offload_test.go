//go:build linux

package transport

import (
	"encoding/binary"
	"net"
	"testing"
	"unsafe"

	"golang.org/x/net/ipv4"
	"golang.org/x/sys/unix"
)

func TestSegmentPaddingFitsAuthenticatedWireFormat(t *testing.T) {
	sender, receiver := pair(t)
	sender.segmentPadding = true
	var encoded, scratch, plain [2048]byte
	for size := 0; size <= MaxPayload; size++ {
		payload := make([]byte, size)
		packet, err := sender.SealInto(encoded[:0], scratch[:], payload, testNow)
		if err != nil || len(packet) > MaxPacket || (len(packet)%32 != 0 && len(packet) != MaxPacket) {
			t.Fatalf("length %d: wire %d: %v", size, len(packet), err)
		}
		decoded, err := receiver.OpenInto(plain[:0], packet, testNow)
		if err != nil || len(decoded) != size {
			t.Fatalf("length %d: %v", size, err)
		}
	}
}

func TestGSOGroupsPreserveAuthenticatedDatagramsAndReplayWindow(t *testing.T) {
	sender, receiver := pair(t)
	sender.segmentPadding = true
	messages := make([]ipv4.Message, sendPacketCapacity)
	for i := range messages {
		payload := make([]byte, 1200)
		payload[0] = byte(i)
		packet, err := sender.Seal(payload, testNow)
		if err != nil {
			t.Fatal(err)
		}
		messages[i] = ipv4.Message{Buffers: [][]byte{packet}, Addr: &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 39000}}
	}
	var packed segmentBatch
	seen := map[byte]bool{}
	output, err := packed.pack(messages)
	if err != nil {
		t.Fatal(err)
	}
	for _, message := range output {
		data := message.Buffers[0]
		size := len(data)
		if len(message.OOB) > 0 {
			control, err := unix.ParseSocketControlMessage(message.OOB)
			if err != nil || len(control) != 1 || control[0].Header.Type != unix.UDP_SEGMENT {
				t.Fatal("invalid GSO metadata", err)
			}
			size = int(binary.NativeEndian.Uint16(control[0].Data))
		}
		if size > MaxPacket || len(data)%size != 0 {
			t.Fatal("invalid boundaries")
		}
		for offset := 0; offset < len(data); offset += size {
			plain, err := receiver.Open(data[offset:offset+size], testNow)
			if err != nil || len(plain) != 1200 || seen[plain[0]] {
				t.Fatal("lost, repeated or rejected datagram", err)
			}
			seen[plain[0]] = true
			if _, err := receiver.Open(data[offset:offset+size], testNow); err == nil {
				t.Fatal("replay accepted")
			}
		}
	}
	if len(seen) != sendPacketCapacity {
		t.Fatal("datagrams lost")
	}
}

func TestGSOBounds(t *testing.T) {
	var packed segmentBatch
	if _, err := packed.pack(make([]ipv4.Message, sendPacketCapacity+1)); err == nil {
		t.Fatal("oversized batch accepted")
	}
	if _, err := packed.pack([]ipv4.Message{{Buffers: [][]byte{make([]byte, MaxPacket+1)}}}); err == nil {
		t.Fatal("oversized datagram accepted")
	}
}

func TestGRORejectsTruncationAndInvalidMetadata(t *testing.T) {
	control := make([]byte, unix.CmsgSpace(2))
	header := (*unix.Cmsghdr)(unsafe.Pointer(&control[0]))
	header.Level, header.Type = unix.SOL_UDP, unix.UDP_GRO
	header.SetLen(unix.CmsgLen(2))
	binary.NativeEndian.PutUint16(control[unix.CmsgLen(0):], 1000)
	valid := ipv4.Message{Buffers: [][]byte{make([]byte, 3000)}, N: 3000, OOB: control, NN: len(control)}
	if size, err := datagramSize(&valid); err != nil || size != 1000 {
		t.Fatal(size, err)
	}
	for name, change := range map[string]func(*ipv4.Message){
		"payload truncation":      func(m *ipv4.Message) { m.Flags = unix.MSG_TRUNC },
		"metadata truncation":     func(m *ipv4.Message) { m.Flags = unix.MSG_CTRUNC },
		"missing buffer":          func(m *ipv4.Message) { m.Buffers = nil },
		"invalid length":          func(m *ipv4.Message) { m.N = 4000 },
		"missing boundary":        func(m *ipv4.Message) { m.NN = 0 },
		"invalid metadata length": func(m *ipv4.Message) { m.NN = len(control) + 1 },
		"short metadata":          func(m *ipv4.Message) { m.NN = 1 },
		"duplicate boundary":      func(m *ipv4.Message) { m.OOB = append(append([]byte{}, control...), control...); m.NN = len(m.OOB) },
	} {
		t.Run(name, func(t *testing.T) {
			m := valid
			change(&m)
			if _, err := datagramSize(&m); err == nil {
				t.Fatal("invalid metadata accepted")
			}
		})
	}
}

func FuzzDatagramSize(f *testing.F) {
	f.Add(make([]byte, 64), []byte{}, 64, 0, 0)
	f.Add([]byte{1}, []byte{1}, 1, 1, 0)
	f.Fuzz(func(t *testing.T, payload, control []byte, n, nn, flags int) {
		if len(payload) > 65535 || len(control) > 128 {
			t.Skip()
		}
		message := ipv4.Message{Buffers: [][]byte{payload}, N: n, OOB: control, NN: nn, Flags: flags}
		size, err := datagramSize(&message)
		if err == nil && (size <= 0 || size > MaxPacket || n > len(payload) || (n+size-1)/size > 64) {
			t.Fatal("invalid accepted boundaries")
		}
	})
}
