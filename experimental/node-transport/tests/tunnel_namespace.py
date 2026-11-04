"""Изолированный нагрузочный тест двух TUN peer; сеть хоста не меняется."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import statistics
import subprocess
import tempfile
import threading
import time
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--binary", default="./mdnt-tunnel")
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--rate", default="300M")
    args = parser.parse_args()
    if not args.run:
        print("План: два namespace, veth, TUN MTU1380, UDP iperf120с, RTT/CPU/порты, очистка.")
        return
    if os.geteuid() != 0 or not 20 <= args.duration <= 300:
        raise RuntimeError("Требуется root; длительность 20–300 секунд")
    binary = str(Path(args.binary).resolve(strict=True))
    namespaces = []
    children = []
    streams = []
    stopped = threading.Event()
    watcher = None
    def execute(ns, *command):
        return run("ip", "netns", "exec", ns, *command)
    def launch(ns, *command, **kwargs):
        process = subprocess.Popen(["ip", "netns", "exec", ns, *command], **kwargs)
        children.append(process)
        return process
    def cpu(process):
        values = Path(f"/proc/{process.pid}/stat").read_text().split()
        return (int(values[13]) + int(values[14])) / os.sysconf("SC_CLK_TCK")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    with tempfile.TemporaryDirectory(prefix="mdnt-tun-test-") as temporary:
        root = Path(temporary)
        key = root / "key"
        key.write_text(secrets.token_hex(32))
        key.chmod(0o600)
        try:
            prefix = "mnt" + uuid.uuid4().hex[:7]
            for suffix in ("a", "b"):
                ns = prefix + suffix
                run("ip", "netns", "add", ns)
                namespaces.append(ns)
                execute(ns, "ip", "link", "set", "lo", "up")
            a, b = namespaces
            execute(a, "ip", "link", "add", "outera", "type", "veth", "peer", "name", "outerb")
            execute(a, "ip", "link", "set", "outerb", "netns", b)
            peers = []
            for index, ns in enumerate(namespaces, 1):
                interface = "outer" + ("a" if index == 1 else "b")
                execute(ns, "ip", "addr", "add", f"10.250.231.{index}/30", "dev", interface)
                execute(ns, "ip", "link", "set", interface, "up")
                state = root / str(index)
                state.mkdir(mode=0o700)
                log = (root / f"peer{index}.log").open("w+")
                streams.append(log)
                process = launch(ns, binary, "--tun", "mdnt0", "--bind", f"10.250.231.{index}",
                    "--peer", f"10.250.231.{3-index}", "--direction", "uplink" if index == 1 else "downlink",
                    "--key-file", str(key), "--state", str(state), stdout=log, stderr=log)
                peers.append(process)
                for _ in range(100):
                    if subprocess.run(["ip", "netns", "exec", ns, "ip", "link", "show", "mdnt0"], capture_output=True).returncode == 0:
                        break
                    time.sleep(0.05)
                execute(ns, "ip", "addr", "add", f"172.29.231.{index}/30", "dev", "mdnt0")
                execute(ns, "ip", "link", "set", "mdnt0", "mtu", "1380", "up")
            execute(a, "ping", "-c", "3", "-W", "2", "-M", "do", "-s", "1352", "172.29.231.2")
            server = launch(b, "iperf3", "-s", "-B", "172.29.231.2", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ports = [set(), set()]
            def watch():
                while not stopped.wait(1):
                    for i, ns in enumerate(namespaces):
                        ports[i].add(execute(ns, "ss", "-ulnH").stdout)
            watcher = threading.Thread(target=watch)
            watcher.start()
            ping = launch(a, "ping", "-i", "0.2", "-c", str(args.duration*5), "-W", "1", "172.29.231.2", stdout=subprocess.PIPE, text=True)
            start = time.monotonic()
            before = [cpu(peer) for peer in peers]
            client = launch(a, "iperf3", "-c", "172.29.231.2", "-u", "-b", args.rate, "-l", "1200", "-t", str(args.duration), "-J", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output, errors = client.communicate(timeout=args.duration+20)
            elapsed = time.monotonic()-start
            percentages = [100*(cpu(peer)-value)/elapsed for peer,value in zip(peers,before)]
            ping_output, _ = ping.communicate(timeout=10)
            data = json.loads(output)
            if client.returncode or "error" in data:
                raise RuntimeError(data.get("error", errors))
            rtt = [float(value) for value in re.findall(r"time=([0-9.]+) ms", ping_output)]
            result = {"duration": elapsed, "received": data["end"]["sum_received"], "cpu_percent_one_core": percentages,
                "port_states": [len(values) for values in ports], "ping_received":len(rtt),
                "rtt_median_ms":statistics.median(rtt) if rtt else None, "rtt_max_ms":max(rtt) if rtt else None}
            print(json.dumps(result), flush=True)
            assert result["received"]["lost_percent"] <= 0.5, "Потери выше критерия 0,5%"
            assert all(len(values) >= 2 for values in ports), "Ротация не наблюдалась"
        finally:
            stopped.set()
            if watcher:
                watcher.join(timeout=20)
            for process in reversed(children):
                if process.poll() is None:
                    process.terminate()
            for process in reversed(children):
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for stream in streams:
                stream.close()
            for ns in reversed(namespaces):
                run("ip", "netns", "del", ns)


if __name__ == "__main__":
    main()
