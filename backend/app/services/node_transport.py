"""Transport definitions for cluster nodes; legacy routes stay until migration."""

from uuid import UUID
import os
import re

REALITY_SERVER_NAME = "www.cloudflare.com"
NODE_UPLINK_TAG = "uplink-warp"
UPLINK_FROM_ENV = object()


def resolve_uplink_port(port=UPLINK_FROM_ENV) -> int | None:
    if port is UPLINK_FROM_ENV:
        return node_uplink_warp_port()
    if port is None or type(port) is int and port == 0:
        return None
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Некорректный локальный порт WARP")
    return port


def node_uplink_warp_port() -> int | None:
    """Необязательный локальный WARP для межсерверного канала, отдельно от выхода ноды."""
    value = os.getenv("NODE_UPLINK_WARP_PORT", "").strip()
    if value in {"", "0"}:
        return None
    if len(value) > 5 or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError("NODE_UPLINK_WARP_PORT должен быть локальным портом от 1 до 65535 или пустым")
    return int(value)


def node_uplink_outbounds(port=UPLINK_FROM_ENV) -> list[dict]:
    port = resolve_uplink_port(port)
    if port is None:
        return []
    return [{"tag": NODE_UPLINK_TAG, "protocol": "socks", "settings": {
        "servers": [{"address": "127.0.0.1", "port": port}]}}]


def _with_node_uplink(outbound: dict, port=UPLINK_FROM_ENV) -> dict:
    if resolve_uplink_port(port) is not None:
        outbound["streamSettings"].setdefault("sockopt", {})["dialerProxy"] = NODE_UPLINK_TAG
    return outbound


def node_outbound(node: dict, *, uplink_port=UPLINK_FROM_ENV) -> dict:
    tag = f"node-{node['id']}"
    if node.get("protocol") == "vless":
        key = node.get("public_key")
        if not key:
            raise ValueError("У защищённой ноды отсутствует публичный ключ Reality")
        short_id = node.get("short_id") or ""
        if not re.fullmatch(r"(?:[0-9a-f]{2}){0,8}", short_id):
            raise ValueError("Некорректный shortId ноды Reality")
        identity = str(UUID(node["secret"]))
        return _with_node_uplink({
            "tag": tag, "protocol": "vless",
            "settings": {"vnext": [{"address": node["host"], "port": int(node["port"]),
                                    # Межсерверный канал должен передавать QUIC, а не
                                    # принудительно переводить клиентов на TCP.
                                    "users": [{"id": identity, "encryption": "none", "flow": "xtls-rprx-vision-udp443"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality",
                               "realitySettings": {"serverName": REALITY_SERVER_NAME,
                                                   "fingerprint": "firefox", "password": key, "shortId": short_id}},
        }, uplink_port)
    return _with_node_uplink({
        "tag": tag, "protocol": "trojan",
        "settings": {"servers": [{"address": node["host"], "port": int(node["port"]), "password": node["secret"]}]},
        "streamSettings": {"network": "grpc", "grpcSettings": {"serviceName": "MD-Next-Node"}},
    }, uplink_port)
