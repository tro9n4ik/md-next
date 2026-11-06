package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestStatusCommandRoundTrip(t *testing.T) {
	var calls atomic.Int32
	id := strings.Repeat("a", 32)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+strings.Repeat("1", 64) {
			t.Error("missing dedicated authentication")
		}
		var payload pollRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		if payload.Version != 1 || payload.Snapshot.AgentVersion != version || payload.Snapshot.UptimeMS < 0 {
			t.Error("invalid state")
		}
		if calls.Add(1) == 1 {
			_ = json.NewEncoder(w).Encode(pollResponse{1, 15000, []command{{id, "status", time.Now().Add(time.Minute).UnixMilli()}}})
		} else {
			if payload.Completed != id {
				t.Error("command was not acknowledged")
			}
			_ = json.NewEncoder(w).Encode(pollResponse{1, 15000, []command{}})
			cancel()
		}
	}))
	defer server.Close()
	done := make(chan error, 1)
	go func() { done <- run(ctx, config{server.URL, strings.Repeat("1", 64)}, server.Client()) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("agent did not complete status command")
	}
	if calls.Load() != 2 {
		t.Fatalf("poll count %d", calls.Load())
	}
}

func TestRejectsInvalidResponses(t *testing.T) {
	for _, body := range []string{
		`{"version":2,"poll_after_ms":15000,"commands":[]}`,
		`{"version":1,"poll_after_ms":0,"commands":[]}`,
		`{"version":1,"poll_after_ms":15000,"commands":[{"id":"bad","kind":"status"}]}`,
		`{"version":1,"poll_after_ms":15000,"commands":[{"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"shell"}]}`,
		strings.Repeat("x", 8193),
		`{"version":1,"poll_after_ms":15000,"commands":[]} {}`,
	} {
		server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(body)) }))
		_, err := exchange(context.Background(), server.Client(), config{server.URL, "test"}, pollRequest{})
		server.Close()
		if err == nil {
			t.Fatal("accepted invalid response")
		}
	}
}

func TestDoesNotFollowRedirectOrLeakKey(t *testing.T) {
	var redirected atomic.Bool
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/leak" {
			redirected.Store(true)
			return
		}
		http.Redirect(w, r, "/leak", http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	client := newPanelClient()
	client.Transport = server.Client().Transport
	_, err := exchange(context.Background(), client, config{server.URL, strings.Repeat("1", 64)}, pollRequest{})
	if err == nil || redirected.Load() {
		t.Fatal("redirect was followed with the agent credential")
	}
}

func TestConfigurationRequiresVerifiedHTTPSAndPrivateKey(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX private file permissions")
	}
	path := filepath.Join(t.TempDir(), "agent.json")
	for _, address := range []string{"http://panel.example/api/v1/nodes/1/control/poll", "https://user:pass@panel.example/api/v1/nodes/1/control/poll", "https://panel.example/api/v1/nodes/1/control/poll?token=secret", "https://panel.example/other"} {
		data, _ := json.Marshal(config{address, strings.Repeat("1", 64)})
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := loadConfig(path); err == nil {
			t.Fatal("accepted unsafe configuration")
		}
	}
	data, _ := json.Marshal(config{"https://panel.example/api/v1/nodes/1/control/poll", strings.Repeat("1", 64)})
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadConfig(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := loadConfig(path); err == nil {
		t.Fatal("accepted publicly readable agent key")
	}
}
