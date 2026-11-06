package main

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"time"
)

const (
	statusMagic    = "MDN1"
	statusRequest  = 1
	statusResponse = 2
	statusHeader   = len(statusMagic) + 1 + 16
	statusSize     = statusHeader + 8
	statusTTL      = 10 * time.Second
)

func newStatusRequest(now time.Time) ([]byte, error) {
	p := make([]byte, statusSize)
	copy(p, statusMagic)
	p[len(statusMagic)] = statusRequest
	if _, err := rand.Read(p[len(statusMagic)+1 : statusHeader]); err != nil {
		return nil, err
	}
	binary.BigEndian.PutUint64(p[statusHeader:], uint64(now.Add(statusTTL).UnixMilli()))
	return p, nil
}

func statusReply(p []byte, now, started time.Time) []byte {
	if len(p) != statusSize || !bytes.HasPrefix(p, []byte(statusMagic)) || p[len(statusMagic)] != statusRequest {
		return nil
	}
	deadline := time.UnixMilli(int64(binary.BigEndian.Uint64(p[statusHeader:])))
	if !deadline.After(now) || deadline.After(now.Add(2*statusTTL)) || started.After(now) {
		return nil
	}
	reply := append([]byte(nil), p...)
	reply[len(statusMagic)] = statusResponse
	binary.BigEndian.PutUint64(reply[statusHeader:], uint64(now.Sub(started)/time.Millisecond))
	return reply
}

func readStatusReply(request, reply []byte, now time.Time) (uint64, error) {
	if len(request) != statusSize || !bytes.HasPrefix(request, []byte(statusMagic)) || request[len(statusMagic)] != statusRequest ||
		len(reply) != statusSize || !bytes.HasPrefix(reply, []byte(statusMagic)) ||
		reply[len(statusMagic)] != statusResponse || !bytes.Equal(request[len(statusMagic)+1:statusHeader], reply[len(statusMagic)+1:statusHeader]) {
		return 0, errors.New("status response does not match the request")
	}
	deadline := time.UnixMilli(int64(binary.BigEndian.Uint64(request[statusHeader:])))
	if !deadline.After(now) || deadline.After(now.Add(2*statusTTL)) {
		return 0, errors.New("status request expired or has an invalid deadline")
	}
	return binary.BigEndian.Uint64(reply[statusHeader:]), nil
}
