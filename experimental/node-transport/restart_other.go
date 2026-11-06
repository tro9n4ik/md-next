//go:build !linux

package transport

import (
	"errors"
	"os"
)

func lockSlotStore(path string) (*os.File, error) {
	return nil, errors.New("persistent receiver requires Linux")
}
