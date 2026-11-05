//go:build linux

package transport

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"runtime/pprof"
	"sync"
	"time"
)

// StartDiagnostics writes bounded, private counters and sampled profiles, never packet contents.
func StartDiagnostics(parent context.Context, directory string, stats *TunnelStats) (func() error, error) {
	if directory == "" {
		return func() error { return nil }, nil
	}
	if err := os.Mkdir(directory, 0700); err != nil && !os.IsExist(err) {
		return nil, err
	}
	info, err := os.Lstat(directory)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0077 != 0 {
		return nil, fmt.Errorf("private diagnostics directory required")
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, err
	}
	if len(entries) != 0 {
		return nil, fmt.Errorf("diagnostics directory must be empty")
	}
	open := func(name string) (*os.File, error) {
		return os.OpenFile(filepath.Join(directory, name), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	}
	output, err := open("metrics.jsonl")
	if err != nil {
		return nil, err
	}
	encoder := json.NewEncoder(output)
	sample := func() error {
		var memory runtime.MemStats
		runtime.ReadMemStats(&memory)
		descriptors, e := os.ReadDir("/proc/self/fd")
		if e != nil {
			return e
		}
		dropped := uint64(0)
		if stats != nil {
			dropped = stats.InvalidFrames.Load()
		}
		return encoder.Encode(map[string]any{"time": time.Now().UTC(), "heap_alloc": memory.HeapAlloc, "heap_inuse": memory.HeapInuse, "total_alloc": memory.TotalAlloc,
			"mallocs": memory.Mallocs, "frees": memory.Frees, "goroutines": runtime.NumGoroutine(), "fd": len(descriptors), "invalid_frames": dropped})
	}
	profile := func(index int) error {
		runtime.GC()
		for _, kind := range []string{"heap", "allocs"} {
			file, e := open(fmt.Sprintf("%s-%03d.pb.gz", kind, index))
			if e != nil {
				return e
			}
			e = pprof.Lookup(kind).WriteTo(file, 0)
			closeErr := file.Close()
			if e != nil {
				return e
			}
			if closeErr != nil {
				return closeErr
			}
		}
		return nil
	}
	if err = profile(0); err != nil {
		output.Close()
		return nil, err
	}
	if err = sample(); err != nil {
		output.Close()
		return nil, err
	}
	ctx, cancel := context.WithCancel(parent)
	done := make(chan struct{})
	var recorded error
	go func() {
		defer close(done)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for i := 1; i <= 3600; i++ {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
			if e := sample(); e != nil {
				recorded = e
				return
			}
			if i%60 == 0 {
				if e := profile(i / 60); e != nil {
					recorded = e
					return
				}
			}
		}
	}()
	var once sync.Once
	return func() error {
		once.Do(func() {
			cancel()
			<-done
			if recorded == nil {
				recorded = profile(999)
			}
			if recorded == nil {
				recorded = sample()
			}
			output.Close()
		})
		return recorded
	}, nil
}
