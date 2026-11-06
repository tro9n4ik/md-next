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
	f, err := os.OpenFile("/dev/net/tun", os.O_RDWR|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	request := struct {
		Name    [16]byte
		Flags   uint16
		Padding [22]byte
	}{Flags: 0x0001 | 0x1000}
	copy(request.Name[:], name)
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), uintptr(0x400454ca), uintptr(unsafe.Pointer(&request)))
	if errno != 0 {
		f.Close()
		return nil, errno
	}
	return f, nil
}
