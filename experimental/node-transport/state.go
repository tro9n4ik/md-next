package transport

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
)

// ClaimReceiverKey fails closed on reuse after restart. Do not delete these
// markers to reuse a key: rotate the secret for BOTH peers instead.
func ClaimReceiverKey(dir string, master []byte, direction string) error {
	if len(master) != 32 || (direction != "uplink" && direction != "downlink") {
		return errors.New("invalid receiver identity")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	info, err := os.Lstat(dir)
	if err != nil {
		return err
	}
	if !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return errors.New("private state directory required")
	}
	digest := sha256.Sum256(append(append([]byte("mdnext-receiver/"+direction+"/"), master...), 0))
	path := filepath.Join(dir, hex.EncodeToString(digest[:])+".used")
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errors.New("receiver key already used or state unavailable: rotate shared secret")
	}
	_, writeErr := file.WriteString("Key claimed; rotate before receiver restart.\n")
	syncErr := file.Sync()
	closeErr := file.Close()
	if writeErr != nil {
		return writeErr
	}
	if syncErr != nil {
		return syncErr
	}
	if closeErr != nil {
		return closeErr
	}
	directory, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
