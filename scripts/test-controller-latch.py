"""Изолированная проверка probe и независимого отката через 180 секунд."""
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def run(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    if not parser.parse_args().run:
        print("План: отдельный namespace, недоступный контроллер, таймер 180 с, очистка.")
        return
    root = Path(__file__).resolve().parent
    namespace = "mdlp" + uuid.uuid4().hex[:8]
    unit = None
    run("ip", "netns", "add", namespace)
    try:
        with tempfile.TemporaryDirectory(prefix="mdnext-latch-") as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            source = Path(directory) / "latch.py"
            source.write_text((root / "network-latch.py").read_text().replace(
                'Path("/var/lib/md-next/network-latch")', "Path(" + repr(str(state)) + ")"))
            spec = importlib.util.spec_from_file_location("isolated_latch", source)
            latch = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(latch)

            def command(*args, input=None):
                if args[0] == "systemd-run":
                    index = args.index("/usr/bin/python3")
                    args = (*args[:index], "/usr/sbin/ip", "netns", "exec", namespace, *args[index:])
                    return run(*args, input=input)
                return run("ip", "netns", "exec", namespace, *args, input=input)

            latch.run = command
            command("ip", "link", "set", "lo", "up")
            command("ip", "addr", "add", "192.0.2.1/24", "dev", "lo")
            token = latch.arm()
            unit = latch.unit(token)
            command("nft", "-f", "-", input="table inet mdnext_probe {\n chain output {\n type filter hook output priority 0; policy drop;\n }\n}\n")
            start = time.monotonic()
            result = subprocess.run(["ip", "netns", "exec", namespace, sys.executable,
                str(root / "controller-probe.py"), "--url", "https://controller.invalid", "--ip", "192.0.2.2"],
                capture_output=True, timeout=4)
            elapsed = time.monotonic() - start
            assert result.returncode != 0 and 1.8 <= elapsed <= 2.7, elapsed
            print(f"PROBE_BLOCKED_EXIT_SECONDS={elapsed:.3f}", flush=True)
            command("ip", "link", "set", "lo", "down")
            deadline = time.monotonic() + 205
            while (state / token / "status").read_text() == "armed" and time.monotonic() < deadline:
                time.sleep(1)
            assert (state / token / "status").read_text() == "restored"
            assert "mdnext_probe" not in command("nft", "list", "ruleset").stdout
            print("INDEPENDENT_180S_ROLLBACK_OK", flush=True)
    finally:
        if unit:
            subprocess.run(["systemctl", "stop", unit+".timer", unit+".service"], capture_output=True, timeout=20)
            subprocess.run(["systemctl", "reset-failed", unit+".service"], capture_output=True, timeout=20)
        run("ip", "netns", "del", namespace)


if __name__ == "__main__":
    main()
