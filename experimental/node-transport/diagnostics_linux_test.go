//go:build linux

package transport

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestDiagnosticsPrivateAndJoined(t *testing.T) {
	base := t.TempDir()
	directory := filepath.Join(base, "profiles")
	finish, err := StartDiagnostics(context.Background(), directory, new(TunnelStats))
	if err != nil {
		t.Fatal(err)
	}
	if err = finish(); err != nil {
		t.Fatal(err)
	}
	if err = finish(); err != nil {
		t.Fatal("second shutdown", err)
	}
	entries, err := os.ReadDir(directory)
	if err != nil || len(entries) != 5 {
		t.Fatalf("expected initial and final profiles plus metrics: %v %d", err, len(entries))
	}
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatalf("private profile required: %s %v", entry.Name(), err)
		}
	}
	if _, err := StartDiagnostics(context.Background(), directory, nil); err == nil {
		t.Fatal("must not overwrite profiles")
	}
	link := filepath.Join(base, "link")
	if err := os.Symlink(directory, link); err != nil {
		t.Fatal(err)
	}
	if _, err := StartDiagnostics(context.Background(), link, nil); err == nil {
		t.Fatal("must reject symlink")
	}
}
