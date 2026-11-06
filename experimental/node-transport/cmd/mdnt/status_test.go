package main

import (
	"bytes"
	"encoding/binary"
	"testing"
	"time"
)

func TestStatusRoundTrip(t *testing.T) {
	now := time.Unix(1750000000, 0)
	request, err := newStatusRequest(now)
	if err != nil {
		t.Fatal(err)
	}
	original := append([]byte(nil), request...)
	reply := statusReply(request, now.Add(time.Second), now.Add(-time.Minute))
	uptime, err := readStatusReply(request, reply, now.Add(time.Second))
	if err != nil || uptime != 61000 {
		t.Fatalf("uptime=%d error=%v", uptime, err)
	}
	if !bytes.Equal(request, original) {
		t.Fatal("reply changed the original request")
	}
}

func TestStatusRejectsExpiredReply(t *testing.T) {
	now := time.Unix(1750000000, 0)
	request, err := newStatusRequest(now)
	if err != nil {
		t.Fatal(err)
	}
	reply := statusReply(request, now, now)
	for _, received := range []time.Time{now.Add(statusTTL), now.Add(2 * statusTTL)} {
		if _, err := readStatusReply(request, reply, received); err == nil {
			t.Fatal("accepted an expired status response")
		}
	}
}

func TestStatusRejectsMalformedAndUnrelatedMessages(t *testing.T) {
	now := time.Unix(1750000000, 0)
	request, err := newStatusRequest(now)
	if err != nil {
		t.Fatal(err)
	}
	reply := statusReply(request, now, now)
	for _, input := range [][]byte{nil, request[:statusSize-1], append(append([]byte(nil), request...), 0), reply} {
		if statusReply(input, now, now) != nil {
			t.Fatal("server accepted a malformed request")
		}
	}
	wrongID := append([]byte(nil), reply...)
	wrongID[len(statusMagic)+1] ^= 1
	for _, input := range [][]byte{nil, reply[:statusSize-1], request, wrongID} {
		if _, err := readStatusReply(request, input, now); err == nil {
			t.Fatal("client accepted a malformed or unrelated response")
		}
	}
	badRequest := append([]byte(nil), request...)
	badRequest[0] ^= 1
	if _, err := readStatusReply(badRequest, reply, now); err == nil {
		t.Fatal("accepted a response to an invalid request")
	}
}

func TestStatusRequestDeadlineBounds(t *testing.T) {
	now := time.Unix(1750000000, 0)
	request, err := newStatusRequest(now)
	if err != nil {
		t.Fatal(err)
	}
	for _, deadline := range []time.Time{now, now.Add(-time.Second), now.Add(2*statusTTL + time.Millisecond)} {
		binary.BigEndian.PutUint64(request[statusHeader:], uint64(deadline.UnixMilli()))
		if statusReply(request, now, now) != nil {
			t.Fatal("accepted an expired or excessively future request")
		}
	}
	binary.BigEndian.PutUint64(request[statusHeader:], uint64(now.Add(statusTTL).UnixMilli()))
	if statusReply(request, now, now.Add(time.Second)) != nil {
		t.Fatal("accepted a negative uptime")
	}
}
