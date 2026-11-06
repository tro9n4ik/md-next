// KedrLink v1 is an outbound HTTPS management agent. It does not forward VPN traffic.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"syscall"
	"time"
)

const version = "0.1.0"

type config struct {
	PollURL string `json:"poll_url"`
	Token   string `json:"token"`
}

type snapshot struct {
	AgentVersion string `json:"agent_version"`
	UptimeMS     int64  `json:"uptime_ms"`
}

type pollRequest struct {
	Version   int      `json:"version"`
	Snapshot  snapshot `json:"snapshot"`
	Completed string   `json:"completed_command_id,omitempty"`
}

type command struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	ExpiresMS int64  `json:"expires_at_ms"`
}

type pollResponse struct {
	Version   int       `json:"version"`
	PollAfter int64     `json:"poll_after_ms"`
	Commands  []command `json:"commands"`
}

var hexKey = regexp.MustCompile(`^[0-9a-f]{64}$`)
var commandID = regexp.MustCompile(`^[0-9a-f]{32}$`)
var pollPath = regexp.MustCompile(`^/api/v1/nodes/[1-9][0-9]*/control/poll$`)

func loadConfig(path string) (config, error) {
	var c config
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 || info.Mode().Perm()&0077 != 0 {
		return c, errors.New("private regular configuration file required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return c, errors.New("cannot read agent configuration")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&c) != nil || decoder.Decode(new(any)) != io.EOF {
		return c, errors.New("invalid agent configuration")
	}
	u, err := url.Parse(c.PollURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || !pollPath.MatchString(u.EscapedPath()) || !hexKey.MatchString(c.Token) {
		return c, errors.New("HTTPS panel poll URL and dedicated agent key required")
	}
	return c, nil
}

func exchange(ctx context.Context, client *http.Client, c config, payload pollRequest) (pollResponse, error) {
	var reply pollResponse
	data, err := json.Marshal(payload)
	if err != nil {
		return reply, errors.New("cannot encode agent state")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.PollURL, bytes.NewReader(data))
	if err != nil {
		return reply, errors.New("invalid panel request")
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("Content-Type", "application/json")
	response, err := client.Do(req)
	if err != nil {
		return reply, errors.New("panel connection failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return reply, fmt.Errorf("panel returned HTTP %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 8193))
	if err != nil || len(body) > 8192 {
		return reply, errors.New("invalid panel response size")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&reply) != nil || decoder.Decode(new(any)) != io.EOF || reply.Version != 1 || reply.PollAfter < 1000 || reply.PollAfter > 60000 || len(reply.Commands) > 1 {
		return reply, errors.New("unsupported panel response")
	}
	for _, cmd := range reply.Commands {
		if cmd.Kind != "status" || !commandID.MatchString(cmd.ID) {
			return reply, errors.New("unsupported command")
		}
	}
	return reply, nil
}

func run(ctx context.Context, c config, client *http.Client) error {
	started := time.Now()
	var completed string
	backoff := time.Second
	for {
		reply, err := exchange(ctx, client, c, pollRequest{Version: 1, Snapshot: snapshot{version, time.Since(started).Milliseconds()}, Completed: completed})
		if ctx.Err() != nil {
			return nil
		}
		delay := 15 * time.Second
		if err != nil {
			// Never log response bodies, config, keys or raw HTTP errors.
			fmt.Fprintln(os.Stderr, "KedrLink:", err)
			delay = backoff
			backoff *= 2
			if backoff > time.Minute {
				backoff = time.Minute
			}
		} else {
			backoff = time.Second
			completed = ""
			delay = time.Duration(reply.PollAfter) * time.Millisecond
			for _, cmd := range reply.Commands {
				remaining := time.Until(time.UnixMilli(cmd.ExpiresMS))
				if remaining > 0 && remaining <= time.Minute {
					// status is read-only and idempotent, so retrying its ACK is safe.
					completed = cmd.ID
					delay = time.Second
				}
			}
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
	}
}

func main() {
	path := flag.String("config", "/etc/md-next/kedrlink-agent.json", "private configuration file")
	showVersion := flag.Bool("version", false, "print agent version")
	once := flag.Bool("once", false, "verify one authenticated HTTPS exchange and exit")
	flag.Parse()
	if *showVersion {
		fmt.Println("KedrLink", version)
		return
	}
	c, err := loadConfig(*path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	client := newPanelClient()
	if *once {
		_, err := exchange(ctx, client, c, pollRequest{Version: 1, Snapshot: snapshot{version, 0}})
		if err != nil && ctx.Err() == nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if err := run(ctx, c, client); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func newPanelClient() *http.Client {
	return &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
}
