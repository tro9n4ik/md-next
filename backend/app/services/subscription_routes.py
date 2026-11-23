"""Дополнительные выходы подписки с отдельными учётными данными на сервере панели."""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import uuid

from sqlalchemy import select

from app.models.setting import Setting

ROUTE_KINDS = {"vless_reality_tcp", "vless_xhttp_reality", "vless_xhttp_tls", "hysteria2"}


def country_flag(code):
    country = (code or "").upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        return "🌐"
    return "".join(chr(127397 + ord(letter)) for letter in country)


async def primary_label(db, client):
    from app.models.node import Node
    node_id = client.exit_node_id
    if node_id is None:
        active = await db.get(Setting, "active_node_id")
        node_id = int(active.value) if active and active.value.isdecimal() else None
    code = await db.scalar(select(Node.country_code).where(Node.id == node_id)) if node_id else None
    return country_flag(code)


def setting_key(client_id):
    return f"client.routes.{client_id}"


def read_routes(settings, client_id):
    try:
        value = json.loads(settings.get(setting_key(client_id), "{}"))
        node_ids = value.get("node_ids", [])
        if not isinstance(node_ids, list) or len(node_ids) > 50 or any(type(item) is not int or item <= 0 for item in node_ids):
            return {"enabled": False, "node_ids": []}
        return {"enabled": value.get("enabled") is True, "node_ids": list(dict.fromkeys(node_ids))}
    except (ValueError, TypeError, AttributeError):
        return {"enabled": False, "node_ids": []}


@dataclass(frozen=True)
class RouteProfile:
    id: int
    kind: str
    is_enabled: bool
    uuid: str | None
    auth: str | None
    route_node_id: int
    ip_address: None = None


def route_profile(profile, node_id):
    # Смена исходного ключа перевыпускает также все дополнительные маршруты.
    secret = profile.uuid if profile.kind.startswith("vless_") else profile.auth
    digest = hmac.new((secret or "").encode(), f"md-next/route/{node_id}".encode(), hashlib.sha256).digest()
    return RouteProfile(profile.id, profile.kind, profile.is_enabled,
                        str(uuid.UUID(bytes=digest[:16], version=4)) if profile.uuid else None,
                        base64.urlsafe_b64encode(digest).decode().rstrip("=") if profile.auth else None, node_id)


def profile_email(profile, client_id, *, cdn=False):
    suffix = f"-node{profile.route_node_id}" if getattr(profile, "route_node_id", None) else ""
    return f"c{client_id}-{'cdn' if cdn else profile.kind}{suffix}@md-next"


def traffic_email(email):
    return re.sub(r"-node[1-9][0-9]*@md-next$", "@md-next", email)


def expand_profiles(profiles, settings):
    expanded = list(profiles)
    for profile, client in profiles:
        choices = read_routes(settings, client.id)
        if choices["enabled"] and profile.kind in ROUTE_KINDS:
            expanded.extend((route_profile(profile, node_id), client) for node_id in choices["node_ids"])
    return expanded


async def remove_node(db, node_id):
    rows = (await db.execute(select(Setting).where(Setting.key.like("client.routes.%")))).scalars()
    for row in rows:
        client_id = row.key.rsplit(".", 1)[-1]
        choices = read_routes({row.key: row.value}, client_id)
        if node_id in choices["node_ids"]:
            choices["node_ids"].remove(node_id)
            choices["enabled"] = choices["enabled"] and bool(choices["node_ids"])
            row.value = json.dumps(choices)


async def additional_links(db, client, profiles, settings, kinds):
    from app.models.node import Node
    from app.services.client_exit import selectable
    from app.services.profiles import make_profile_data, PROFILE_LABELS
    from app.services.cdn import make_cdn_link

    choices = read_routes(settings, client.id)
    if not choices["enabled"]:
        return []
    nodes = (await db.execute(select(Node).where(Node.id.in_(choices["node_ids"])).order_by(Node.priority, Node.id))).scalars()
    links = []
    for node in nodes:
        if not selectable(node):
            continue
        flag = country_flag(node.country_code)
        for profile in profiles:
            if profile.kind not in ROUTE_KINDS or profile.kind not in kinds:
                continue
            routed = route_profile(profile, node.id)
            if profile.is_enabled:
                links.append(make_profile_data(client, routed, settings, label=f"{flag} {PROFILE_LABELS[profile.kind]}"))
            cdn = make_cdn_link(client, routed, settings, label=f"{flag} Обход БС")
            if cdn:
                links.append(cdn)
    return links
