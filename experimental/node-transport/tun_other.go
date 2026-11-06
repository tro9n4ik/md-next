//go:build !linux

package transport

import (
	"errors"
	"os"
)

func OpenTUN(name string) (*os.File, error) { return nil, errors.New("TUN prototype requires Linux") }
