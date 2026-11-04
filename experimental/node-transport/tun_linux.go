//go:build linux

package transport

import (
	"errors"
	"os"
	"regexp"
	"syscall"
	"unsafe"
)

// OpenTUN creates only a device. It never changes addresses, routes or firewall.
// The prototype supports inner IPv4 with MTU 1024; IPv6 forwarding is not ready.
func OpenTUN(name string) (*os.File, error) {
	if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,15}$`).MatchString(name) {
		return nil, errors.New("invalid TUN name")
	}
	fd, err := syscall.Open("/dev/net/tun", syscall.O_RDWR|syscall.O_CLOEXEC|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	request := struct {
		Name    [16]byte
		Flags   uint16
		Padding [22]byte
	}{Flags: 0x0001 | 0x1000}
	copy(request.Name[:], name)
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), uintptr(0x400454ca), uintptr(unsafe.Pointer(&request)))
	if errno != 0 {
		syscall.Close(fd)
		return nil, errno
	}
	// Register with Go's poller only after the descriptor is attached to TUN.
	return os.NewFile(uintptr(fd), "/dev/net/tun"), nil
}
