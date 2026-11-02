"""Закрепление VPN-клиентов за нодой: общая смена выхода не меняет их страну."""
from collections import Counter
import ipaddress

from sqlalchemy import select

from app.models.client import Client
from app.models.node import Node
from app.models.setting import Setting
from app.services.client_limits import access_allowed
from app.services.node_transport import node_outbound


def node_summary(node):
    """В интерфейс клиента не передаю адрес, секрет и ключи ноды."""
    if node is None:
        return None
    return {"id": node.id, "name": node.name, "country_code": node.country_code,
            "is_enabled": bool(node.is_enabled), "status": node.status,
            "ping_ms": node.ping_ms, "selectable": selectable(node)}


def selectable(node) -> bool:
    if not node.is_enabled or not node.secret:
        return False
    try:
        node_outbound({"id": node.id, "host": node.host, "port": node.port, "protocol": node.protocol,
                       "secret": node.secret, "public_key": node.public_key, "short_id": node.short_id}, uplink_port=None)
    except (ValueError, TypeError):
        return False
    return True


async def common_exit(db, nodes=None):
    if nodes is None:
        nodes = {node.id: node for node in (await db.execute(select(Node))).scalars()}
    setting = await db.get(Setting, "active_node_id")
    value = setting.value if setting else ""
    node_id = int(value) if value and value.isdecimal() else None
    node = nodes.get(node_id)
    return {"mode": "node" if node else "unavailable" if node_id else "direct", "node": node_summary(node)}


def client_exit_summary(client, nodes, common):
    pinned = client.exit_node_id is not None
    node = node_summary(nodes.get(client.exit_node_id)) if pinned else common["node"]
    state = ("missing" if pinned else "unavailable") if node is None else (
        "disabled" if not node["selectable"] else node["status"])
    if not pinned and common["mode"] == "direct":
        state = "direct"
    return {"mode": "node" if pinned else "common", "node_id": client.exit_node_id,
            "node": node, "status": state}


async def exit_options(db):
    nodes = list((await db.execute(select(Node).order_by(Node.priority, Node.id))).scalars())
    counts = Counter((await db.execute(select(Client.exit_node_id))).scalars())
    common = await common_exit(db, {node.id: node for node in nodes})
    common_id = common["node"]["id"] if common["node"] else None
    return {"nodes": [{**node_summary(node), "assigned_clients": counts[node.id],
                       "following_clients": counts[None] if node.id == common_id else 0} for node in nodes],
            "common": common, "common_clients": counts[None], "total_clients": sum(counts.values())}


def build_client_exit_rules(profiles, kinds, nodes_by_id, global_rules):
    """VLESS/Hysteria сопоставляю по email Xray, AWG — по уникальному IP пира.

    Недоступный или выключенный выход не заменяется общим/direct. Нездоровую
    ноду оставляю в конфигурации: восстановившаяся связь возобновится сама.
    MTProto использует отдельный процесс с общим SOCKS и сюда не входит.
    """
    groups = {}
    for profile, client in profiles:
        node_id = client.exit_node_id
        if node_id is None or not access_allowed(client) or profile.kind not in kinds:
            continue
        users, sources = groups.setdefault(node_id, (set(), set()))
        if profile.kind == "vless_xhttp_tls" and profile.uuid:
            # У CDN свой email; основной XHTTP TLS может быть запрещён отдельно.
            users.add(f"c{client.id}-cdn@md-next")
        if not profile.is_enabled:
            continue
        if profile.kind == "awg" and profile.ip_address:
            sources.add(str(ipaddress.ip_interface(profile.ip_address).ip))
        elif (profile.kind.startswith("vless_") and profile.uuid) or (profile.kind == "hysteria2" and profile.auth):
            users.add(f"c{client.id}-{profile.kind}@md-next")
    rules = []
    for node_id, (users, sources) in sorted(groups.items()):
        node = nodes_by_id.get(node_id)
        target = f"node-{node_id}" if node and selectable(node) else "block"
        selectors = ([{"user": sorted(users)}] if users else []) + (
            [{"inboundTag": ["awg-in"], "source": sorted(sources)}] if sources else [])
        for selector in selectors:
            # Запреты сохраняются, остальные общие правила не меняют выбранный выход.
            rules.extend({**rule, **selector} for rule in global_rules if rule.get("outboundTag") == "block")
            rules.append({"type": "field", **selector, "outboundTag": target})
    return rules
