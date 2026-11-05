//go:build linux

package transport

import (
	"io"
	"os"
	"regexp"
	"sync"
	"syscall"

	wgtun "golang.zx2c4.com/wireguard/tun"
)

// PacketDevice keeps kernel offload details outside the authenticated codec.
type PacketDevice interface {
	ReadPackets([][]byte, []int) (int, error)
	WritePackets([][]byte, int) error
	BatchSize() int
	Close() error
}

type filePackets struct {
	file *os.File
	raw  syscall.RawConn
}

func (d *filePackets) BatchSize() int { return batchSize }
func (d *filePackets) Close() error   { return d.file.Close() }
func (d *filePackets) ReadPackets(buffers [][]byte, sizes []int) (int, error) {
	if len(buffers) == 0 || len(sizes) < len(buffers) {
		return 0, os.ErrInvalid
	}
	count := 0
	var readErr error
	err := d.raw.Read(func(fd uintptr) bool {
		for count < len(buffers) {
			n, e := syscall.Read(int(fd), buffers[count])
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
				readErr = io.EOF
				return true
			}
			sizes[count] = n
			count++
		}
		return true
	})
	if err != nil {
		return count, err
	}
	return count, readErr
}
func (d *filePackets) WritePackets(buffers [][]byte, offset int) error {
	if offset < 0 {
		return os.ErrInvalid
	}
	for _, buffer := range buffers {
		if offset > len(buffer) {
			return os.ErrInvalid
		}
		if _, err := d.file.Write(buffer[offset:]); err != nil {
			return err
		}
	}
	return nil
}

type offloadPackets struct {
	device wgtun.Device
	stop   chan struct{}
	done   chan struct{}
	once   sync.Once
	err    error
}

func (d *offloadPackets) BatchSize() int { return d.device.BatchSize() }
func (d *offloadPackets) Close() error {
	d.once.Do(func() { d.err = d.device.Close(); close(d.stop); <-d.done })
	return d.err
}
func (d *offloadPackets) ReadPackets(buffers [][]byte, sizes []int) (int, error) {
	return d.device.Read(buffers, sizes, 0)
}
func (d *offloadPackets) WritePackets(buffers [][]byte, offset int) error {
	_, err := d.device.Write(buffers, offset)
	return err
}

// OpenPacketTUN only configures the named test device; addresses/routes stay explicit.
func OpenPacketTUN(name string, offload bool) (PacketDevice, error) {
	if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,15}$`).MatchString(name) {
		return nil, os.ErrInvalid
	}
	if offload {
		device, err := wgtun.CreateTUN(name, MaxPayload)
		if err != nil {
			return nil, err
		}
		adapter := &offloadPackets{device: device, stop: make(chan struct{}), done: make(chan struct{})}
		go func() {
			defer close(adapter.done)
			for {
				select {
				case _, ok := <-device.Events():
					if !ok {
						return
					}
				case <-adapter.stop:
					return
				}
			}
		}()
		return adapter, nil
	}
	file, err := OpenTUN(name)
	if err != nil {
		return nil, err
	}
	raw, err := file.SyscallConn()
	if err != nil {
		file.Close()
		return nil, err
	}
	return &filePackets{file: file, raw: raw}, nil
}
