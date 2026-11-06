//go:build linux

package main

import (
	"bufio"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestSIGTERMUnblocksUDPRead(t *testing.T) {
	if os.Getenv("MDNT_SHUTDOWN_CHILD") == "1" {
		flag.CommandLine = flag.NewFlagSet("mdnt", flag.ExitOnError)
		os.Args = []string{"mdnt", "-mode", "serve", "-address", "127.0.0.1:0",
			"-key-file", os.Getenv("MDNT_TEST_KEY"), "-state-dir", os.Getenv("MDNT_TEST_STATE")}
		if err := run(); err != nil {
			t.Fatal(err)
		}
		return
	}
	dir := t.TempDir()
	key := filepath.Join(dir, "key")
	if err := os.WriteFile(key, []byte(strings.Repeat("17", 32)), 0600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestSIGTERMUnblocksUDPRead$")
	cmd.Env = append(os.Environ(), "MDNT_SHUTDOWN_CHILD=1", "MDNT_TEST_KEY="+key, "MDNT_TEST_STATE="+filepath.Join(dir, "state"),
		"GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })
	ready := make(chan bool, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		ready <- scanner.Scan() && strings.Contains(scanner.Text(), "receiver ready")
	}()
	select {
	case ok := <-ready:
		if !ok {
			t.Fatal("receiver did not become ready")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("receiver startup timed out")
	}
	started := time.Now()
	if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(200 * time.Millisecond):
		t.Fatal("SIGTERM did not stop blocked UDP read within 200ms")
	}
	t.Logf("SIGTERM exited successfully in %s", time.Since(started))
}
