import os
import logging
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client import Client, ClientProfile
from app.models.node import Node
from app.models.routing import RoutingRule
from app.models.setting import Setting
from app.services.awg import AWGService
from app.services.profiles import create_profiles, enabled_profile_kinds, get_profile_settings, make_profile_data
from app.services.xray import XrayService
from app.services.routing_rules import to_xray_rule
from app.services.events import log_event
from app.services.nginx import apply_reality_sni
from app.services.client_limits import access_allowed
from app.services.cdn import cdn_access_allowed

_USE_STORED_ACTIVE_NODE = object()
logger = logging.getLogger(__name__)


class ClientService:
    @classmethod
    async def restore_committed_configs(cls, db: AsyncSession) -> None:
        """Восстанавливает параметры внешних сервисов после отката базы данных."""
        for label, sync in (("Xray", cls.sync_xray_clients), ("AmneziaWG", AWGService.sync_server_config)):
            try:
                ok, _ = await sync(db)
                if not ok:
                    logger.error("Не удалось восстановить сохранённую конфигурацию %s", label)
            except Exception:
                logger.error("Не удалось восстановить сохранённую конфигурацию %s", label)

    @staticmethod
    async def sync_xray_clients(db: AsyncSession, active_node=_USE_STORED_ACTIVE_NODE) -> Tuple[bool, str]:
        result = await db.execute(
            select(ClientProfile, Client)
            .join(Client, Client.id == ClientProfile.client_id)
            .where(Client.is_active.is_(True))
            .order_by(ClientProfile.id)
        )
        profiles = result.all()
        kinds = await enabled_profile_kinds(db)
        tcp_clients: list[dict] = []
        xhttp_reality_clients: list[dict] = []
        xhttp_tls_clients: list[dict] = []
        cdn_clients: list[dict] = []
        hysteria_clients: list[dict] = []
        settings = await get_profile_settings(db)
        if "vless_reality_tcp" in kinds:
            try:
                await apply_reality_sni(settings["protocol.reality.server_name"])
            except (OSError, RuntimeError, ValueError) as exc:
                return False, f"Не удалось согласовать Reality SNI с Nginx: {exc}"
        reality_flow = settings.get("protocol.reality.flow", "xtls-rprx-vision")
        for profile, client in profiles:
            if not access_allowed(client):
                continue
            email = f"c{client.id}-{profile.kind}@md-next"
            if profile.kind == "vless_xhttp_tls" and profile.uuid and settings.get("cdn.enabled") == "true" and cdn_access_allowed(client, profile, settings):
                cdn_clients.append({"id": profile.uuid, "email": email})
            if not profile.is_enabled:
                continue
            if profile.kind == "vless_reality_tcp" and profile.uuid:
                client_data = {"id": profile.uuid, "email": email}
                if reality_flow:
                    client_data["flow"] = reality_flow
                tcp_clients.append(client_data)
            elif profile.kind == "vless_xhttp_reality" and profile.uuid:
                # XHTTP оборачивает TLS-соединение; Vision требует прямого TLS/Reality.
                xhttp_reality_clients.append({"id": profile.uuid, "email": email})
            elif profile.kind == "vless_xhttp_tls" and profile.uuid:
                xhttp_tls_clients.append({"id": profile.uuid, "email": email})
            elif profile.kind == "hysteria2" and profile.auth:
                hysteria_clients.append({"auth": profile.auth, "email": email})

        stored_settings = (await db.execute(select(Setting).where(Setting.key.in_(["warp.usage", "warp.proxy_port", "warp.node_id", "warp.node_port"])))).scalars().all()
        setting_values = {item.key: item.value for item in stored_settings}
        warp_usage = setting_values.get("warp.usage", "off")
        warp_port = int(setting_values.get("warp.proxy_port", "40000"))
        warp_node_id = int(setting_values["warp.node_id"]) if setting_values.get("warp.node_id") else None
        if warp_node_id:
            warp_port = int(setting_values.get("warp.node_port", "40000"))
        failover_rows = (await db.execute(select(Setting).where(Setting.key.like("failover.%")))).scalars().all()
        failover_values = {row.key.removeprefix("failover."): row.value for row in failover_rows}
        # fallback_action=keep запрещает выпускать клиентов через реальный IP мастер-сервера,
        # поэтому при смерти ноды трафик обрывается, а не утекает напрямую.
        node_fallback_tag = "direct" if failover_values.get("fallback_action", os.getenv("FAILOVER_FALLBACK_ACTION", "direct")) == "direct" else "block"
        routing_rules = (await db.execute(
            select(RoutingRule).where(RoutingRule.is_active.is_(True)).order_by(RoutingRule.id)
        )).scalars().all()
        node_rows = (await db.execute(select(Node).where(Node.is_enabled.is_(True)).order_by(Node.id))).scalars().all()
        nodes_by_id = {node.id: node for node in node_rows}
        try:
            if warp_usage != "off" and warp_node_id and (warp_node_id not in nodes_by_id or not nodes_by_id[warp_node_id].secret):
                raise ValueError("Выбранная нода WARP отключена или отсутствует; переключите выход WARP")
            xray_routing_rules = [to_xray_rule(rule, nodes_by_id) for rule in routing_rules]
            if warp_usage == "off" and any(rule.action == "warp" for rule in routing_rules):
                raise ValueError("Включите использование WARP (по правилам или для всего трафика), чтобы применить правила WARP")
        except ValueError as exc:
            log_event("error", "xray", "Не удалось подготовить правила маршрутизации", {"reason": str(exc)[:400]})
            return False, str(exc)
        use_stored_node = active_node is _USE_STORED_ACTIVE_NODE
        if use_stored_node:
            active_node = await XrayService.get_active_node(db)
        selected_setting = await db.get(Setting, "active_node_id")
        options = {
            "awg_routing": "awg" in kinds,
            "enabled": kinds,
            "vless_xhttp_reality_clients": xhttp_reality_clients,
            "vless_xhttp_tls_clients": xhttp_tls_clients,
            "cdn_clients": cdn_clients,
            "hysteria2_clients": hysteria_clients,
            "xhttp_reality_port": settings["profiles.port.vless_xhttp_reality"],
            "xhttp_reality_path": settings["profiles.path.vless_xhttp_reality"],
            "xhttp_reality_mode": settings["protocol.xhttp.reality_mode"],
            "xhttp_tls_path": settings["profiles.path.vless_xhttp_tls"],
            "xhttp_tls_mode": settings["protocol.xhttp.tls_mode"],
            "hysteria2_port": settings["profiles.port.hysteria2"],
            "reality_private_key": settings.get("protocol.reality.private_key", os.getenv("XRAY_PRIVATE_KEY", "")),
            "reality_server_name": settings["protocol.reality.server_name"],
            "reality_dest": settings["protocol.reality.target"],
            "reality_short_id": settings["protocol.reality.short_id"],
            "reality_fingerprint": settings["protocol.reality.fingerprint"],
            "reality_public_key": settings["protocol.reality.public_key"],
            "tls_cert": os.getenv("TLS_CERT_PATH", "/etc/letsencrypt/live/" + os.getenv("SERVER_HOST", "") + "/fullchain.pem"),
            "tls_key": os.getenv("TLS_KEY_PATH", "/etc/letsencrypt/live/" + os.getenv("SERVER_HOST", "") + "/privkey.pem"),
            "warp_usage": warp_usage,
            "warp_port": warp_port,
            "warp_node_id": warp_node_id,
            "node_fallback_tag": node_fallback_tag,
            "unavailable_selected_node": use_stored_node and active_node is None and bool(selected_setting and (selected_setting.value or "").isdecimal()),
            "routing_rules": xray_routing_rules,
            "nodes": [{"id": node.id, "host": node.host, "port": node.port, "protocol": node.protocol, "secret": node.secret} for node in node_rows if node.secret],
        }
        success, message = await XrayService.apply_config(clients=tcp_clients, active_node=active_node, profile_options=options)
        log_event("info" if success else "error", "xray", "Конфигурация Xray успешно применена" if success else "Ошибка применения конфигурации Xray", {} if success else {"reason": message[:400]})
        return success, message

    @classmethod
    async def _create_client(cls, db: AsyncSession, name: str, phone: str, email: str) -> tuple[Client, list[ClientProfile], dict[str, str]]:
        client = Client(name=name.strip(), phone=phone or "", email=email or "", protocol=None)
        db.add(client)
        await db.flush()
        profiles = await create_profiles(db, client)
        ok, reason = await cls.sync_xray_clients(db)
        if not ok:
            raise RuntimeError(reason)
        ok, reason = await AWGService.sync_server_config(db)
        if not ok:
            raise RuntimeError(reason)
        settings = await get_profile_settings(db)
        return client, profiles, settings

    @classmethod
    async def create_vless_client(cls, db: AsyncSession, name: str, phone: str = "", email: str = "") -> Tuple[Client, str]:
        protocol_settings = await get_profile_settings(db)
        if not all(protocol_settings.get(key) for key in ("protocol.reality.server_address", "protocol.reality.public_key", "protocol.reality.server_name")):
            raise ValueError("Не заполнены публичный адрес, Reality public key или SNI")
        try:
            client, profiles, settings = await cls._create_client(db, name, phone, email)
            profile = next((item for item in profiles if item.kind == "vless_reality_tcp"), None)
            if profile is None:
                raise ValueError("Профиль VLESS Reality TCP отключён")
            link = make_profile_data(client, profile, settings)
            await db.commit()
            return client, link
        except Exception:
            await db.rollback()
            await cls.restore_committed_configs(db)
            raise

    @classmethod
    async def create_awg_client(cls, db: AsyncSession, name: str, phone: str = "", email: str = "") -> Tuple[Client, str]:
        try:
            client, profiles, settings = await cls._create_client(db, name, phone, email)
            profile = next((item for item in profiles if item.kind == "awg"), None)
            if profile is None:
                raise ValueError("Профиль AmneziaWG отключён")
            conf = make_profile_data(client, profile, settings)
            await db.commit()
            return client, conf
        except Exception:
            await db.rollback()
            await cls.restore_committed_configs(db)
            raise
