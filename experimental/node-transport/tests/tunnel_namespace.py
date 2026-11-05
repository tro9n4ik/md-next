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
import sys
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
    parser.add_argument("--profile-dir", help="existing private directory for optional CPU profiles")
    parser.add_argument("--strict-performance", action="store_true", help="also enforce CPU <=15%% and RTT deviation <=20ms")
    parser.add_argument("--offload", action="store_true", help="enable experimental TUN and UDP offload")
    parser.add_argument("--tcp", action="store_true", help="measure a TCP flow; UDP loss is not inferred from TCP")
    parser.add_argument("--pin-cpus", action="store_true", help="pin peers and load generators to four separate test CPUs")
    parser.add_argument("--warmup", type=int, default=0, help="explicit unmeasured warmup seconds, 0..30")
    parser.add_argument("--diagnostics-dir", help="private directory for bounded peer metrics and heap profiles")
    parser.add_argument("--latency-acceptance", action="store_true", help="require median <=50ms and p99 <=70ms")
    parser.add_argument("--jumbo-check", action="store_true", help="inject oversized L3 frames before the measured traffic")
    args = parser.parse_args()
    if not args.run:
        print("План: два namespace, veth, TUN MTU1380, UDP iperf120с, RTT/CPU/порты, очистка.")
        return
    if os.geteuid() != 0 or not 20 <= args.duration <= 300 or not 0 <= args.warmup <= 30:
        raise RuntimeError("Требуется root; длительность 20–300 секунд")
    binary = str(Path(args.binary).resolve(strict=True))
    cpus = sorted(os.sched_getaffinity(0))
    if args.pin_cpus and len(cpus) < 4:
        raise RuntimeError("Для разнесения peer и генераторов нужны четыре доступных CPU")
    namespaces = []
    children = []
    streams = []
    stopped = threading.Event()
    watcher = None
    def execute(ns, *command):
        return run("ip", "netns", "exec", ns, *command)
    def launch(ns, *command, cpu=None, **kwargs):
        if args.pin_cpus and cpu is not None:
            command = ("taskset", "-c", str(cpus[cpu]), *command)
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
                profile = ["--cpu-profile", str(Path(args.profile_dir).resolve() / f"peer{index}.pprof")] if args.profile_dir else []
                if args.offload:
                    profile += ["--tun-offload", "--udp-gso"]
                if args.diagnostics_dir:
                    parent = Path(args.diagnostics_dir)
                    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    profile += ["--diagnostics-dir", str(parent.resolve() / f"peer{index}")]
                process = launch(ns, binary, *profile, "--tun", "mdnt0", "--bind", f"10.250.231.{index}",
                    "--peer", f"10.250.231.{3-index}", "--direction", "uplink" if index == 1 else "downlink",
                    "--key-file", str(key), "--state", str(state), cpu=index-1, stdout=log, stderr=log)
                peers.append(process)
                for _ in range(100):
                    if subprocess.run(["ip", "netns", "exec", ns, "ip", "link", "show", "mdnt0"], capture_output=True).returncode == 0:
                        break
                    time.sleep(0.05)
                execute(ns, "ip", "addr", "add", f"172.29.231.{index}/30", "dev", "mdnt0")
                execute(ns, "ip", "link", "set", "mdnt0", "mtu", "1380", "up")
            execute(a, "ping", "-c", "3", "-W", "2", "-M", "do", "-s", "1352", "172.29.231.2")
            if args.jumbo_check:
                execute(a,"ip","link","set","mdnt0","mtu","9000")
                try:
                    execute(a,sys.executable,"-c","import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); [(s.sendto(bytes(n-28),('172.29.231.2',39875))) for n in (1500,2048,4096,9000)]")
                    time.sleep(0.1)
                    if any(peer.poll() is not None for peer in peers):
                        raise RuntimeError("Peer завершился при проверке больших кадров")
                finally:
                    execute(a,"ip","link","set","mdnt0","mtu","1380")
            server = launch(b, "iperf3", "-s", "-B", "172.29.231.2", cpu=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if execute(b,"ss","-ltnH","sport","=",":5201").stdout.strip():
                    break
                if server.poll() is not None:
                    raise RuntimeError("Сервер iperf3 завершился до начала измерения")
                time.sleep(0.02)
            else:
                raise RuntimeError("Сервер iperf3 не открыл тестовый порт")
            traffic = [] if args.tcp else ["-u", "-l", "1200"]
            if args.warmup:
                warm = launch(a, "iperf3", "-c", "172.29.231.2", *traffic, "-b", args.rate, "-w", "8M", "-t", str(args.warmup), cpu=2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                _, errors = warm.communicate(timeout=args.warmup+15)
                if warm.returncode:
                    raise RuntimeError("Прогрев не выполнен: " + errors)
            ports = [set(), set()]
            def watch():
                while not stopped.wait(1):
                    for i, ns in enumerate(namespaces):
                        rows = execute(ns, "ss", "-ulnH").stdout.splitlines()
                        ports[i].add(tuple(sorted(row.split()[3] for row in rows)))
            watcher = threading.Thread(target=watch)
            watcher.start()
            ping_stream = tempfile.TemporaryFile(mode="w+t")
            streams.append(ping_stream)
            ping = launch(a, "ping", "-i", "0.2", "-c", str(args.duration*5), "-W", "1", "172.29.231.2", stdout=ping_stream, text=True)
            start = time.monotonic()
            before = [cpu(peer) for peer in peers]
            client = launch(a, "iperf3", "-c", "172.29.231.2", *traffic, "-b", args.rate, "-w", "8M", "-t", str(args.duration), "-J", cpu=2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output, errors = client.communicate(timeout=args.duration+20)
            elapsed = time.monotonic()-start
            percentages = [100*(cpu(peer)-value)/elapsed for peer,value in zip(peers,before)]
            ping.wait(timeout=10)
            ping_stream.seek(0)
            ping_output = ping_stream.read()
            data = json.loads(output)
            if client.returncode or "error" in data:
                raise RuntimeError(data.get("error", errors))
            rtt = [float(value) for value in re.findall(r"time=([0-9.]+) ms", ping_output)]
            result = {"duration": elapsed, "received": data["end"]["sum_received"], "cpu_percent_one_core": percentages,
                "port_states": [len(values) for values in ports], "ping_received":len(rtt),
                "rtt_median_ms":statistics.median(rtt) if rtt else None, "rtt_max_ms":max(rtt) if rtt else None}
            result["traffic"] = "TCP" if args.tcp else "UDP"
            result["pinned_cpus"] = cpus[:4] if args.pin_cpus else None
            result["gomaxprocs"] = os.environ.get("GOMAXPROCS","default")
            result["warmup_seconds"] = args.warmup
            result["rtt_p99_ms"] = sorted(rtt)[min(len(rtt)-1,int(len(rtt)*0.99))] if rtt else None
            result["rss_peak_kib"] = [int(next(line.split()[1] for line in Path(f"/proc/{peer.pid}/status").read_text().splitlines() if line.startswith("VmHWM:"))) for peer in peers]
            result["kernel_udp"] = [execute(ns, "cat", "/proc/net/snmp").stdout for ns in namespaces]
            result["tun_counters"] = [json.loads(execute(ns, "ip", "-s", "-j", "link", "show", "mdnt0").stdout)[0].get("stats64") for ns in namespaces]
            result["criteria"] = {
                "loss": None if args.tcp else result["received"]["lost_percent"] <= 0.5,
                "rotation": all(len(values) >= 2 for values in ports),
                "cpu": all(value <= 15 for value in percentages),
                "rtt": bool(rtt) and max(rtt)-statistics.median(rtt) <= 20,
            }
            print(json.dumps(result), flush=True)
            if not args.tcp:
                assert result["criteria"]["loss"], "Потери выше критерия 0,5%"
            assert result["criteria"]["rotation"], "Ротация не наблюдалась"
            if args.latency_acceptance:
                assert rtt and result["rtt_median_ms"] <= 50 and result["rtt_p99_ms"] <= 70, "RTT выше критериев стабилизации"
            if args.strict_performance:
                assert result["criteria"]["cpu"] and result["criteria"]["rtt"], "CPU или RTT выше критериев ТЗ"
        finally:
            failed = sys.exc_info()[0] is not None
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
            for index,stream in enumerate(streams,1):
                if failed:
                    stream.seek(0)
                    print(f"Тестовый peer {index}:\n"+stream.read(4096),file=sys.stderr)
                stream.close()
            for ns in reversed(namespaces):
                run("ip", "netns", "del", ns)


if __name__ == "__main__":
    main()
