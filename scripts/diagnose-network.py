#!/usr/bin/env python3
"""Read-only MD-Next network report. Never prints credentials or subscription URLs."""

import argparse
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import urlparse


def run(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=18)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, "", type(error).__name__


def read_env(path):
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Also test HTTPS through the current Xray SOCKS route",
    )
    args = parser.parse_args()
    working = run(
        "systemctl", "show", "md-next-backend", "-p", "WorkingDirectory", "--value"
    )[1]
    app_dir = args.app_dir or (
        Path(working).parent if working else Path("/opt/md-next")
    )
    env = read_env(app_dir / "backend/.env")
    config_path = Path(env.get("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"))
    config = json.loads(config_path.read_text())
    db = sqlite3.connect(f'file:{app_dir / "backend/md_next.db"}?mode=ro', uri=True)
    try:
        settings = dict(db.execute("select key,value from settings"))
        nodes = [
            dict(zip(("id", "enabled", "status", "priority"), row))
            for row in db.execute(
                "select id,is_enabled,status,priority from nodes order by priority,id"
            )
        ]
        profiles = db.execute(
            "select p.kind,count(*) from client_profiles p join clients c on c.id=p.client_id where p.is_enabled=1 and c.is_active=1 group by p.kind"
        ).fetchall()
    finally:
        db.close()
    report = {
        "xray_version": run("xray", "version")[1].splitlines()[:1],
        "services": {
            name: run("systemctl", "is-active", name)[1]
            for name in ("md-next-backend", "xray", "nginx")
        },
        "selected_node": settings.get("active_node_id", ""),
        "warp_usage": settings.get("warp.usage", "off"),
        "nodes": nodes,
        "enabled_client_profiles": dict(profiles),
        "default_outbound": config.get("outbounds", [{}])[0].get("tag"),
        "observatory_enabled": bool(
            config.get("burstObservatory") or config.get("observatory")
        ),
        "routing": config.get("routing", {}),
        "inbounds": [],
        "checks": [],
    }
    sni = settings.get("protocol.reality.server_name") or env.get(
        "XRAY_SERVER_NAME", ""
    )
    public = settings.get("protocol.reality.public_key") or env.get(
        "XRAY_PUBLIC_KEY", ""
    )
    stream_path = Path(
        env.get(
            "NGINX_STREAM_CONFIG", "/etc/nginx/stream-available/md-next-stream.conf"
        )
    )
    stream = stream_path.read_text() if stream_path.is_file() else ""
    import re

    report["reality_sni"] = sni
    report["checks"].append(
        {
            "sni_routes_to_xray": (
                bool(
                    re.search(
                        r"(?mi)^\s*" + re.escape(sni) + r"\s+xray_backend\s*;", stream
                    )
                )
                if sni
                else False
            )
        }
    )
    for inbound in config.get("inbounds", []):
        transport = inbound.get("streamSettings", {})
        reality = transport.get("realitySettings", {})
        clients = inbound.get("settings", {}).get("clients", [])
        summary = {
            "tag": inbound.get("tag"),
            "protocol": inbound.get("protocol"),
            "listen": inbound.get("listen"),
            "port": inbound.get("port"),
            "network": transport.get("network"),
            "security": transport.get("security"),
            "clients_count": len(clients),
            "flow_values": sorted({client.get("flow", "") for client in clients}),
            "sniffing": inbound.get("sniffing"),
        }
        if reality:
            summary.update(
                {
                    "sni": reality.get("serverNames"),
                    "target": reality.get("target", reality.get("dest")),
                    "xver": reality.get("xver", 0),
                }
            )
            try:
                from cryptography.hazmat.primitives.asymmetric import x25519
                from cryptography.hazmat.primitives import serialization

                key = reality.get("privateKey", "")
                derived = (
                    x25519.X25519PrivateKey.from_private_bytes(
                        base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
                    )
                    .public_key()
                    .public_bytes(
                        serialization.Encoding.Raw, serialization.PublicFormat.Raw
                    )
                )
                summary["public_key_matches_subscription"] = (
                    base64.urlsafe_b64encode(derived).decode().rstrip("=") == public
                )
            except (ImportError, ValueError):
                summary["public_key_matches_subscription"] = (
                    "Не проверено: запустите Python из backend/venv"
                )
        report["inbounds"].append(summary)
    report["dns"] = {
        key: value for key, value in settings.items() if key.startswith("dns.")
    }
    # No raw config, privateKey, passwords, UUIDs, or tokens enter this report.
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if args.probe:
        for url in ("https://www.cloudflare.com/cdn-cgi/trace", "https://example.com/"):
            code, out, error = run(
                "curl",
                "--noproxy",
                "",
                "-sS",
                "--max-time",
                "12",
                "--socks5-hostname",
                "127.0.0.1:10808",
                "-w",
                "\nHTTP_STATUS=%{http_code}",
                url,
            )
            lines = [
                line
                for line in out.splitlines()
                if line.startswith(("ip=", "loc=", "warp=", "HTTP_STATUS="))
            ]
            print(
                json.dumps(
                    {"probe": url, "exit_code": code, "result": lines, "error": error},
                    ensure_ascii=False,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
