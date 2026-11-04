//go:build linux

package transport

import (
	"encoding/binary"
	"net"
	"unsafe"

	"golang.org/x/net/ipv4"
	"golang.org/x/sys/unix"
)

func enableGRO(conn *net.UDPConn) error {
	raw, err := conn.SyscallConn()
	if err != nil {
		return err
	}
	var optionErr error
	if err = raw.Control(func(fd uintptr) {
		optionErr = unix.SetsockoptInt(int(fd), unix.SOL_UDP, unix.UDP_GRO, 1)
	}); err != nil {
		return err
	}
	return optionErr
}

func datagramSize(message *ipv4.Message) (int, error) {
	if len(message.Buffers) != 1 || message.Flags&(unix.MSG_TRUNC|unix.MSG_CTRUNC) != 0 || message.N <= 0 || message.N > len(message.Buffers[0]) || message.NN < 0 || message.NN > len(message.OOB) {
		return 0, ErrPacket
	}
	size, found := message.N, false
	for control := message.OOB[:message.NN]; len(control) > 0; {
		if len(control) < unix.CmsgLen(0) { return 0,ErrPacket }
		header, data, remaining, err := unix.ParseOneSocketControlMessage(control)
		if err != nil {
			return 0, ErrPacket
		}
		control = remaining
		if header.Level == unix.SOL_UDP && header.Type == unix.UDP_GRO {
			if found || len(data) < 2 {
				return 0, ErrPacket
			}
			size, found = int(binary.NativeEndian.Uint16(data)), true
		}
	}
	if size <= 0 || size > MaxPacket || (message.N+size-1)/size > 64 {
		return 0, ErrPacket
	}
	return size, nil
}

type segmentBatch struct {
	payload  [sendPacketCapacity * MaxPacket]byte
	messages [sendPacketCapacity]ipv4.Message
	buffers  [sendPacketCapacity][1][]byte
	control  [sendPacketCapacity][64]byte
}

func (b *segmentBatch) pack(source []ipv4.Message) ([]ipv4.Message, error) {
	if len(source) > sendPacketCapacity {
		return nil, ErrPacket
	}
	for _, message := range source {
		if len(message.Buffers) != 1 || len(message.Buffers[0]) == 0 || len(message.Buffers[0]) > MaxPacket {
			return nil, ErrPacket
		}
	}
	offset, count := 0, 0
	// Reorder only inside 32-packet chunks, within the 64-packet replay window.
	for begin := 0; begin < len(source); begin += batchSize {
		chunk := source[begin:min(begin+batchSize, len(source))]
		used := uint32(0)
		for i := range chunk {
			if used&(1<<i) != 0 {
				continue
			}
			size := len(chunk[i].Buffers[0])
			start, segments := offset, 0
			for j := i; j < len(chunk); j++ {
				if used&(1<<j) == 0 && len(chunk[j].Buffers[0]) == size {
					offset += copy(b.payload[offset:], chunk[j].Buffers[0])
					used |= 1 << j
					segments++
				}
			}
			b.buffers[count][0] = b.payload[start:offset]
			b.messages[count] = ipv4.Message{Buffers: b.buffers[count][:], Addr: chunk[i].Addr}
			if segments > 1 {
				control := b.control[count][:unix.CmsgSpace(2)]
				clear(control)
				header := (*unix.Cmsghdr)(unsafe.Pointer(&control[0]))
				header.Level, header.Type = unix.SOL_UDP, unix.UDP_SEGMENT
				header.SetLen(unix.CmsgLen(2))
				binary.NativeEndian.PutUint16(control[unix.CmsgLen(0):], uint16(size))
				b.messages[count].OOB = control
			}
			count++
		}
	}
	return b.messages[:count], nil
}
