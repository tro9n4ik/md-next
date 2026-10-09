"""Transport definitions for cluster nodes; legacy routes stay until migration."""

from uuid import UUID

REALITY_SERVER_NAME = "www.cloudflare.com"


def node_outbound(node: dict) -> dict:
    tag = f"node-{node['id']}"
    if node.get("protocol") == "vless":
        key = node.get("public_key")
        if not key:
            raise ValueError("У защищённой ноды отсутствует публичный ключ Reality")
        identity = str(UUID(node["secret"]))
        return {
            "tag": tag, "protocol": "vless",
            "settings": {"vnext": [{"address": node["host"], "port": int(node["port"]),
                                    "users": [{"id": identity, "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality",
                               "realitySettings": {"serverName": REALITY_SERVER_NAME,
                                                   "fingerprint": "firefox", "password": key, "shortId": ""}},
        }
    return {
        "tag": tag, "protocol": "trojan",
        "settings": {"servers": [{"address": node["host"], "port": int(node["port"]), "password": node["secret"]}]},
        "streamSettings": {"network": "grpc", "grpcSettings": {"serviceName": "MD-Next-Node"}},
    }
