"""Root-only интеграционная проверка MSS в трёх временных namespace."""
import argparse
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=20, **kwargs)


def wait_for_capture(capture, timeout=5):
    """Ждать готовности libpcap, прежде чем отправлять проверочный пакет."""
    deadline = time.monotonic() + timeout
    messages = b""
    with selectors.DefaultSelector() as selector:
        selector.register(capture.stderr, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(capture.stderr.fileno(), 4096)
            if not chunk:
                raise RuntimeError("tcpdump завершился до готовности захвата: " + messages.decode(errors="replace"))
            messages += chunk
            if b"listening on " in messages:
                return
            if len(messages) > 16384:
                raise RuntimeError("Слишком большой вывод запуска tcpdump")
    raise RuntimeError("tcpdump не подтвердил готовность захвата за отведённое время")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print("План: три изолированных namespace; IPv4/IPv6 SYN, DF 1352, nft/iptables; затем удаление.")
        return
    if os.geteuid() != 0:
        raise RuntimeError("Требуется root")
    prefix = "mdm" + uuid.uuid4().hex[:7]
    left, router, right = [prefix + suffix for suffix in ("l", "m", "r")]
    created = []
    def execute(namespace, *command):
        return run("ip", "netns", "exec", namespace, *command)
    def ip(namespace, *command):
        return execute(namespace, "ip", *command)
    try:
        for namespace in (left, router, right):
            run("ip", "netns", "add", namespace)
            created.append(namespace)
            ip(namespace, "link", "set", "lo", "up")
        for namespace, local, remote, segment in ((left, "ml", "awg0", 1), (right, "mr", "mdawg", 2)):
            ip(router, "link", "add", remote, "type", "veth", "peer", "name", local)
            ip(router, "link", "set", local, "netns", namespace)
            for ns, interface, host in ((router, remote, 1), (namespace, local, 2)):
                ip(ns, "addr", "add", f"10.231.{segment}.{host}/24", "dev", interface)
                ip(ns, "-6", "addr", "add", f"fd42:231:{segment}::{host}/64", "dev", interface, "nodad")
                ip(ns, "link", "set", interface, "up")
            ip(namespace, "route", "add", "default", "via", f"10.231.{segment}.1")
            ip(namespace, "-6", "route", "add", "default", "via", f"fd42:231:{segment}::1")
        execute(router, "sysctl", "-qw", "net.ipv4.ip_forward=1", "net.ipv6.conf.all.forwarding=1")
        # Reproduce early filter verdicts from Docker without touching host Docker.
        for binary in ("iptables", "ip6tables"):
            execute(router, binary, "-N", "DOCKER-USER")
            execute(router, binary, "-A", "DOCKER-USER", "-j", "ACCEPT")
            execute(router, binary, "-I", "FORWARD", "1", "-j", "DOCKER-USER")
            execute(router, binary, "-t", "mangle", "-A", "FORWARD", "-j", "ACCEPT")
        helper = Path(__file__).with_name("network-mtu.py")
        load = "import runpy; m=runpy.run_path(__import__('sys').argv[1]); "
        for fallback in (False, True):
            choice = "m['shutil'].which=lambda name: None if name=='nft' else '/usr/sbin/'+name; " if fallback else ""
            for _ in range(2):
                execute(router, sys.executable, "-c", load + choice + "m['apply']()", str(helper))
            if fallback:
                for binary, ceiling in (("iptables", 1340), ("ip6tables", 1320)):
                    entries = [line for line in execute(router, binary, "-t", "mangle", "-S", "FORWARD").stdout.splitlines()
                               if line.startswith("-A FORWARD ")]
                    assert len(entries) == 3, entries
                    assert all(f"--set-mss {ceiling}" in line for line in entries[:2]), entries
                    assert entries[2] == "-A FORWARD -j ACCEPT", entries
            execute(left, "ping", "-c", "3", "-W", "2", "-M", "do", "-s", "1352", "10.231.2.2")
            for source, destination, interface, v4, v6 in (
                (left, right, "mr", "10.231.2.2", "fd42:231:2::2"),
                (right, left, "ml", "10.231.1.2", "fd42:231:1::2"),
            ):
                for family, address, ceiling in ((4, v4, 1340), (6, v6, 1320)):
                    capture = subprocess.Popen(["ip", "netns", "exec", destination, "tcpdump", "-l", "-nn", "-i", interface,
                        "-c", "1", f"ip{6 if family == 6 else ''} and tcp dst port 39876 and tcp[tcpflags] & tcp-syn != 0" if family == 4
                        else "ip6 and tcp dst port 39876"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        env={**os.environ, "LC_ALL": "C"})
                    try:
                        wait_for_capture(capture)
                        execute(source, sys.executable, "-c", "import socket,sys; s=socket.socket(socket.AF_INET6 if sys.argv[1]=='6' else socket.AF_INET); s.settimeout(2); s.connect_ex((sys.argv[2],39876))", str(family), address)
                        output, _ = capture.communicate(timeout=5)
                        match = re.search(r"mss (\d+)", output)
                        assert match and int(match[1]) == ceiling, output
                    finally:
                        if capture.poll() is None:
                            capture.kill()
                            capture.communicate()
            execute(router, sys.executable, "-c", load + choice + "m['remove']()", str(helper))
            print(("iptables" if fallback else "nftables") + ": DF1352 и MSS IPv4/IPv6 в обе стороны — OK")
    finally:
        for namespace in reversed(created):
            run("ip", "netns", "del", namespace)


if __name__ == "__main__":
    main()
