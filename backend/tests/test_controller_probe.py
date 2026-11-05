import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


def module():
    spec = importlib.util.spec_from_file_location("probe", Path(__file__).parents[2] / "scripts/controller-probe.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_literal_probe_preserves_certificate_hostname_without_dns(monkeypatch):
    import io
    probe = module()
    calls = []
    class Socket:
        def settimeout(self, timeout): calls.append(timeout)
        def connect(self, target): calls.append(target)
        def sendall(self, value): pass
        def makefile(self, *args): return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\n\r\n{\"status\":\"ok\"}")
        def close(self): pass
    class Context:
        def wrap_socket(self, raw, server_hostname):
            calls.append(server_hostname)
            return raw
    monkeypatch.setattr(probe.ssl, "create_default_context", lambda: Context())
    monkeypatch.setattr(probe.socket, "socket", lambda *args: Socket())
    monkeypatch.setattr(probe.socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("DNS called on target"))
    probe.probe("https://controller.example", "192.0.2.25")
    assert ("192.0.2.25", 443) in calls and "controller.example" in calls


def test_probe_watchdog_does_not_confirm_after_timeout(monkeypatch):
    probe = module()
    monkeypatch.setattr(sys, "argv", ["probe", "--url", "https://controller.example", "--ip", "192.0.2.25"])
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 2.0
        raise subprocess.TimeoutExpired(args[0], 2)
    monkeypatch.setattr(probe.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="2 секунды"):
        probe.main()
