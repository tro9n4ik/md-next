#!/usr/bin/python3 -I
"""Фиксированная установка и применение MTProto. Выполняю только из root helper."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.request

VERSION = "1.13.0"
DIGESTS = {
    "x86_64": ("amd64", "8af41d394a54f2cde9ede69dc0ec4db936d05d325d85495046885f57cb17ef33"),
    "aarch64": ("arm64", "fe9b291d7b82ae0bad89d2c031fb1acd84381ba7325840ce4f1d89649b827f69"),
}
BINARY = Path("/usr/local/bin/md-next-mtg")
CONFIG = Path("/etc/md-next-mtproto/config.toml")
UNIT = "md-next-mtproto.service"


def validate(payload):
    if not isinstance(payload, dict) or set(payload) != {"port", "domain", "users"}:
        raise ValueError("Invalid MTProto request")
    port, domain, users = payload["port"], payload["domain"], payload["users"]
    # Отдельный публичный порт; запрещаю локальные API и служебные диапазоны.
    if type(port) is not int or not 1024 <= port <= 65535 or port in {2053, 8000, 8080, 8443, 8444, 10085, 10808, 10909, 10910} or 20000 <= port <= 49999:
        raise ValueError("Reserved MTProto port")
    if not isinstance(users, dict) or len(users) > 10000:
        raise ValueError("Invalid MTProto users")
    if users and (not isinstance(domain, str) or len(domain) > 128 or not re.fullmatch(r"(?=.{1,128}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain)):
        raise ValueError("MTProto requires a public hostname")
    for name, key in users.items():
        if not isinstance(name, str) or not re.fullmatch(r"p[1-9][0-9]{0,18}", name) or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("Invalid MTProto identity")
    if len(set(users.values())) != len(users):
        raise ValueError("Duplicate MTProto secrets")
    return payload


def render(payload):
    validate(payload)
    # SOCKS Xray может подтвердить подключение до фактического соединения ноды
    # с Telegram. Предпочтение IPv6 тогда не даёт перейти к рабочему IPv4.
    lines = [f'bind-to = "0.0.0.0:{payload["port"]}"', 'api-bind-to = "127.0.0.1:10910"',
             'concurrency = 2048', 'prefer-ip = "only-ipv4"', '[network]',
             'proxies = ["socks5://127.0.0.1:10909"]', 'dns = "https://1.1.1.1"', '[secrets]']
    for name, key in sorted(payload["users"].items()):
        lines.append(f'{name} = "ee{key}{payload["domain"].encode("ascii").hex()}"')
    return "\n".join(lines) + "\n"


def command(*args):
    result = subprocess.run(args, capture_output=True, timeout=45, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    if result.returncode:
        raise RuntimeError("MTProto system operation failed")
    return result


def atomic(path, data, mode=0o600, gid=0):
    if path.is_symlink():
        raise ValueError("Unexpected MTProto symlink")
    fd, name = tempfile.mkstemp(prefix=".mtproto-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.chown(name, 0, gid)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare():
    import pwd
    try:
        user = pwd.getpwnam("md-next-mtproto")
    except KeyError:
        command("/usr/sbin/useradd", "--system", "--user-group", "--home-dir", "/nonexistent", "--shell", "/usr/sbin/nologin", "md-next-mtproto")
        user = pwd.getpwnam("md-next-mtproto")
    directory = CONFIG.parent
    if directory.is_symlink():
        raise ValueError("Unexpected MTProto directory")
    directory.mkdir(mode=0o750, exist_ok=True)
    os.chown(directory, 0, user.pw_gid)
    os.chmod(directory, 0o750)
    unit = '''[Unit]
Description=MD-Next Telegram MTProto
After=network-online.target xray.service
Wants=network-online.target
[Service]
User=md-next-mtproto
Group=md-next-mtproto
ExecStart=/usr/local/bin/md-next-mtg run /etc/md-next-mtproto/config.toml
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
UMask=0077
LimitNOFILE=65536
[Install]
WantedBy=multi-user.target
'''
    atomic(Path("/etc/systemd/system") / UNIT, unit.encode(), 0o644)
    command("/usr/bin/systemctl", "daemon-reload")
    return user


def install_binary():
    marker = CONFIG.parent / "version"
    if BINARY.is_file() and marker.is_file() and marker.read_text().strip() == VERSION:
        return False
    architecture, digest = DIGESTS[platform.machine()]
    url = f"https://github.com/dolonet/mtg-multi/releases/download/v{VERSION}/mtg-multi-{VERSION}-linux-{architecture}.tar.gz"
    # Фиксированные версия, URL и SHA256; никаких адресов или команд от API.
    with urllib.request.urlopen(url, timeout=30) as response:
        archive = response.read(32 * 1024 * 1024 + 1)
    if len(archive) > 32 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != digest:
        raise ValueError("MTProto download checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        matches = [member for member in tar if member.isfile() and Path(member.name).name in {"mtg", "mtg-multi"}]
        if len(matches) != 1 or matches[0].size > 64 * 1024 * 1024:
            raise ValueError("Invalid MTProto archive")
        data = tar.extractfile(matches[0]).read()
        notices = [member for member in tar.getmembers() if member.isfile() and Path(member.name).name == "LICENSE"]
        if len(notices) != 1 or notices[0].size > 1024 * 1024:
            raise ValueError("MTProto license missing from archive")
        license_text = tar.extractfile(notices[0]).read()
    atomic(CONFIG.parent / "LICENSE", license_text, 0o644)
    atomic(BINARY, data, 0o755)
    atomic(marker, (VERSION + "\n").encode(), 0o644)
    return True


def verify_applied(payload):
    pairs = [f"{name}=ee{key}{payload['domain'].encode('ascii').hex()}" for name, key in sorted(payload["users"].items())]
    expected = hashlib.sha256("\n".join(pairs).encode()).hexdigest()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            with opener.open("http://127.0.0.1:10910/stats", timeout=1) as response:
                data = json.loads(response.read(4 * 1024 * 1024 + 1))
            if data.get("secrets_sha256") == expected:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("MTProto did not confirm updated secrets")


def apply(payload):
    validate(payload)
    if not payload["users"]:
        if Path("/etc/systemd/system", UNIT).exists():
            command("/usr/bin/systemctl", "disable", "--now", UNIT)
        if CONFIG.exists():
            atomic(CONFIG, b"# MTProto disabled; no client secrets\n")
        return
    user = prepare()
    binary_changed = install_binary()
    text = render(payload).encode()
    before = CONFIG.read_bytes() if CONFIG.exists() else b""
    if before == text and not binary_changed:
        command("/usr/bin/systemctl", "enable", "--now", UNIT)
        try:
            verify_applied(payload)
        except Exception:
            subprocess.run(["/usr/bin/systemctl", "disable", "--now", UNIT], timeout=20, capture_output=True)
            raise
        return
    atomic(CONFIG, text, 0o640, user.pw_gid)
    try:
        active = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", UNIT], timeout=10).returncode == 0
        old_globals = before.split(b"[secrets]", 1)[0]
        globals_changed = old_globals != text.split(b"[secrets]", 1)[0]
        action = "restart" if binary_changed or globals_changed or not active else "reload"
        command("/usr/bin/systemctl", action, UNIT)
        command("/usr/bin/systemctl", "enable", UNIT)
        command("/usr/bin/systemctl", "is-active", "--quiet", UNIT)
        verify_applied(payload)
    except Exception:
        atomic(CONFIG, before, 0o640, user.pw_gid)
        # При ошибке применения не оставляю потенциально отозванный секрет активным.
        subprocess.run(["/usr/bin/systemctl", "disable", "--now", UNIT], timeout=20, capture_output=True)
        raise


if __name__ == "__main__":
    import sys
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    if sys.argv[1:] == ["--prepare-only"]:
        prepare()
    elif sys.argv[1:] == ["--apply"]:
        fd = os.open("/var/lib/md-next/app-config/mtproto.json", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            apply(json.loads(stream.read(4 * 1024 * 1024 + 1)))
    else:
        raise SystemExit("Unknown MTProto operation")
