import os
import secrets
import urllib.parse
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client, ClientProfile
from app.models.node import Node
from app.models.setting import Setting
from app.services.awg import AWGService
from app.services.crypto import encrypt_secret, decrypt_secret
from app.services.reality_keys import resolve_key_pair
from app.services.xray import XrayService


PROFILE_KINDS = (
    "vless_reality_tcp", "vless_xhttp_reality", "vless_xhttp_tls", "hysteria2", "awg"
)
PROFILE_LABELS = {
    "vless_reality_tcp": "VLESS Reality TCP",
    "vless_xhttp_reality": "VLESS XHTTP Reality",
    "vless_xhttp_tls": "VLESS XHTTP TLS",
    "hysteria2": "Hysteria 2",
    "awg": "AmneziaWG",
}


async def enabled_profile_kinds(db: AsyncSession) -> set[str]:
    keys = [f"profiles.enabled.{kind}" for kind in PROFILE_KINDS]
    result = await db.execute(select(Setting).where(Setting.key.in_(keys)))
    values = {setting.key: setting.value.lower() in ("true", "1", "yes") for setting in result.scalars()}
    return {kind for kind in PROFILE_KINDS if values.get(f"profiles.enabled.{kind}", kind in ("vless_reality_tcp", "awg"))}


async def get_profile_settings(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(Setting))
    values = {setting.key: setting.value for setting in result.scalars().all()}
    for kind, fallback in (("vless_xhttp_reality", os.getenv("XRAY_XHTTP_REALITY_PORT", "2053")), ("hysteria2", "443")):
        values.setdefault(f"profiles.port.{kind}", fallback)
    values.setdefault("profiles.path.vless_xhttp_reality", "/")
    values.setdefault("profiles.path.vless_xhttp_tls", os.getenv("XRAY_XHTTP_TLS_PATH", "/md-next-xhttp"))

    server_host_env = os.getenv("SERVER_HOST", "")
    current_address = values.get("protocol.reality.server_address", "")
    if (not current_address or current_address in ("127.0.0.1", "localhost")) and server_host_env:
        values["protocol.reality.server_address"] = server_host_env
    else:
        values.setdefault("protocol.reality.server_address", server_host_env or "127.0.0.1")

    server_name_env = os.getenv("XRAY_SERVER_NAME", "")
    current_sni = values.get("protocol.reality.server_name", "")
    if not current_sni and server_name_env:
        values["protocol.reality.server_name"] = server_name_env
    else:
        values.setdefault("protocol.reality.server_name", server_name_env)

    values.setdefault("protocol.reality.target", os.getenv("XRAY_DEST", "127.0.0.1:8080"))
    values.setdefault("protocol.reality.private_key", os.getenv("XRAY_PRIVATE_KEY", ""))
    values.setdefault("protocol.reality.public_key", os.getenv("XRAY_PUBLIC_KEY", ""))
    # Ссылка и конфиг обязаны опираться на одну пару ключей, иначе Reality
    # не узнаёт клиента и отдаёт ему настоящий сертификат вместо handshake.
    values["protocol.reality.private_key"], values["protocol.reality.public_key"] = resolve_key_pair(
        values["protocol.reality.private_key"], values["protocol.reality.public_key"]
    )
    values.setdefault("protocol.reality.fingerprint", "chrome")
    values.setdefault("protocol.reality.short_id", "")
    values.setdefault("protocol.reality.flow", "xtls-rprx-vision")
    values.setdefault("protocol.xhttp.reality_mode", "auto")
    values.setdefault("protocol.xhttp.tls_mode", "auto")
    return values


async def create_profiles(db: AsyncSession, client: Client) -> list[ClientProfile]:
    enabled = await enabled_profile_kinds(db)
    existing_result = await db.execute(select(ClientProfile.kind).where(ClientProfile.client_id == client.id))
    existing = set(existing_result.scalars().all())
    profiles: list[ClientProfile] = []
    for kind in PROFILE_KINDS:
        if kind not in enabled or kind in existing:
            continue
        profile = ClientProfile(client=client, kind=kind, is_enabled=True)
        if kind.startswith("vless_"):
            profile.uuid = str(uuid.uuid4())
        elif kind == "hysteria2":
            profile.auth = secrets.token_urlsafe(24)
        elif kind == "awg":
            profile.ip_address = await AWGService.allocate_profile_ip(db)
            private_key, profile.public_key = AWGService.generate_keypair()
            profile.private_key_enc = encrypt_secret(private_key)
        db.add(profile)
        profiles.append(profile)
    await db.flush()
    return profiles


def make_profile_data(client: Client, profile: ClientProfile, settings: dict[str, str]) -> str:
    name = urllib.parse.quote(client.name, safe="")
    host = settings.get("protocol.reality.server_address", os.getenv("SERVER_HOST", "127.0.0.1"))
    sni = settings.get("protocol.reality.server_name", os.getenv("XRAY_SERVER_NAME", host))
    public_key = settings.get("protocol.reality.public_key", os.getenv("XRAY_PUBLIC_KEY", ""))
    fingerprint = settings.get("protocol.reality.fingerprint", "chrome")
    short_id = settings.get("protocol.reality.short_id", "")
    flow = settings.get("protocol.reality.flow", "xtls-rprx-vision")
    if profile.kind == "vless_reality_tcp":
        return XrayService.generate_vless_link(
            profile.uuid or "", host, 443, sni, public_key, client.name,
            fingerprint=fingerprint, short_id=short_id, flow=flow,
        )
    if profile.kind == "vless_xhttp_reality":
        port = int(settings.get("profiles.port.vless_xhttp_reality", os.getenv("XRAY_XHTTP_REALITY_PORT", "2053")))
        path = settings.get("profiles.path.vless_xhttp_reality", "/")
        mode = settings.get("protocol.xhttp.reality_mode", "auto")
        params = {
            "encryption": "none", "security": "reality", "sni": sni,
            "fp": fingerprint, "pbk": public_key, "type": "xhttp", "path": path, "mode": mode,
        }
        if short_id:
            params["sid"] = short_id
        return f"vless://{profile.uuid}@{host}:{port}?{urllib.parse.urlencode(params)}#{name}"
    if profile.kind == "vless_xhttp_tls":
        path = settings.get("profiles.path.vless_xhttp_tls", "")
        mode = settings.get("protocol.xhttp.tls_mode", "auto")
        params = {"encryption": "none", "security": "tls", "sni": host, "type": "xhttp", "path": path, "mode": mode}
        return f"vless://{profile.uuid}@{host}:443?{urllib.parse.urlencode(params)}#{name}"
    if profile.kind == "hysteria2":
        port = int(settings.get("profiles.port.hysteria2", "443"))
        return f"hysteria2://{urllib.parse.quote(profile.auth or '', safe='')}@{host}:{port}/?sni={urllib.parse.quote(host, safe='')}#{name}"
    if profile.kind == "awg":
        private_key = decrypt_secret(profile.private_key_enc or "")
        if not private_key:
            return ""
        awg = settings
        endpoint = f"{host}:{awg.get('awg_port', os.getenv('AWG_PORT', '51820'))}"
        return AWGService.generate_client_conf(
            private_key=private_key,
            address=profile.ip_address or "",
            server_public_key=awg.get("awg_server_public_key", ""),
            server_endpoint=endpoint,
            server_private_key=awg.get("awg_server_private_key", ""),
        )
    return ""
