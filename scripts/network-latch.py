"""Независимый таймер возврата firewall перед удалённым изменением сети."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

STATE = Path("/var/lib/md-next/network-latch")


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=True, timeout=20)


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def folder(token):
    if not re.fullmatch("[a-f0-9]{32}", token):
        raise ValueError("Неверный идентификатор страховки")
    path = STATE / token
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise ValueError("Небезопасное состояние страховки")
    return path


def unit(token):
    return "mdnext-net-rollback-" + token


def arm():
    if (STATE / "active").exists():
        raise RuntimeError("Предыдущая страховка ещё действует")
    token = secrets.token_hex(16)
    path = STATE / token
    path.mkdir(mode=0o700)
    try:
        if shutil.which("nft"):
            source = "flush ruleset\n" + run("nft", "list", "ruleset").stdout
            run("nft", "-c", "-f", "-", input=source)
            write(path / "nft.rules", source)
        else:
            for name in ("iptables", "ip6tables"):
                write(path / (name + ".rules"), run(name + "-save").stdout)
        # Таймер использует собственную копию и переживает обрыв SSH.
        write(path / "restore.py", Path(__file__).read_text())
        write(path / "status", "armed")
        write(STATE / "active", token)
        run("systemd-run", "--quiet", "--unit=" + unit(token), "--on-active=180s",
            "--timer-property=AccuracySec=1s", "--property=Type=oneshot",
            "/usr/bin/python3", str(path / "restore.py"), "rollback", token)
    except Exception:
        (STATE / "active").unlink(missing_ok=True)
        shutil.rmtree(path)
        raise
    return token


def rollback(token):
    path = folder(token)
    if (path / "status").read_text() != "armed":
        return
    if (path / "nft.rules").is_file():
        run("nft", "-f", str(path / "nft.rules"))
    else:
        for name in ("iptables", "ip6tables"):
            run(name + "-restore", input=(path / (name + ".rules")).read_text())
    (path / "status").write_text("restored")
    (STATE / "active").unlink(missing_ok=True)


def confirm(token):
    path = folder(token)
    if (path / "status").read_text() not in ("armed", "confirmed"):
        raise RuntimeError("Срок страховки истёк или правила уже восстановлены")
    # Статус меняется под тем же lock, который использует таймер.
    (path / "status").write_text("confirmed")
    (STATE / "active").unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("arm", "confirm", "rollback"))
    parser.add_argument("token", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Требуется root")
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o077:
        raise RuntimeError("Небезопасный каталог страховки")
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "arm":
            print(json.dumps({"token": arm()}))
        elif args.action == "rollback":
            rollback(args.token)
        else:
            confirm(args.token)
    if args.action == "confirm":
        # Остановку службы выполняем после снятия lock.
        run("systemctl", "stop", unit(args.token) + ".timer")
        # После снятия transient-таймера systemd может уже выгрузить ещё не
        # запускавшуюся службу. Код 5 означает отсутствие такой службы.
        result = subprocess.run(["systemctl", "stop", unit(args.token) + ".service"],
                                capture_output=True, text=True, timeout=20)
        if result.returncode not in (0, 5):
            raise RuntimeError("Не удалось остановить службу страховки")
        shutil.rmtree(folder(args.token))


if __name__ == "__main__":
    main()
