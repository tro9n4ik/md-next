import os
from typing import Dict, Any
from sqlalchemy.future import select
from app.services.crypto import decrypt_secret
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting

def mask_token(token: str) -> str:
    if not token or len(token) < 8:
        return "****"
    return f"{token[:4]}...{token[-4:]}"

async def get_telegram_settings_from_db() -> Dict[str, Any]:
    keys = [
        "telegram_bot_token",
        "telegram_admin_id",
        "telegram_proxy_url",
        "telegram_notify_node_down",
        "telegram_notify_failover",
        "telegram_notify_quota",
        "telegram_use_node",
        "telegram_node_id",
    ]
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Setting).where(Setting.key.in_(keys)))
            settings_db = {s.key: s.value for s in res.scalars().all()}
    except Exception:
        settings_db = {}

    enc_token = settings_db.get("telegram_bot_token", "")
    token = decrypt_secret(enc_token) if enc_token else os.getenv("TELEGRAM_BOT_TOKEN", "")

    admin_id_str = settings_db.get("telegram_admin_id", os.getenv("ADMIN_TELEGRAM_ID", "0"))
    try:
        admin_id = int(admin_id_str) if admin_id_str else 0
    except ValueError:
        admin_id = 0

    proxy_url = settings_db.get("telegram_proxy_url", os.getenv("TELEGRAM_PROXY_URL", ""))

    notify_node_down_str = settings_db.get("telegram_notify_node_down", "true").lower()
    notify_node_down = notify_node_down_str in ("true", "1", "yes")

    notify_failover_str = settings_db.get("telegram_notify_failover", "true").lower()
    notify_failover = notify_failover_str in ("true", "1", "yes")
    notify_quota = settings_db.get("telegram_notify_quota", "true").lower() in ("true", "1", "yes")

    return {
        "token": token,
        "admin_id": admin_id,
        "proxy_url": proxy_url,
        "notify_node_down": notify_node_down,
        "notify_failover": notify_failover,
        "notify_quota": notify_quota,
        "use_node": settings_db.get("telegram_use_node", "false").lower() in ("true", "1", "yes"),
        "node_id": int(settings_db["telegram_node_id"]) if settings_db.get("telegram_node_id", "").isdigit() else None,
    }


async def resolve_telegram_proxy(db, settings: Dict[str, Any]) -> str:
    """Выбранная нода использует свой SOCKS-вход Xray без изменения маршрутов клиентов."""
    if not settings.get("use_node"):
        return settings.get("proxy_url", "").strip()
    from app.models.node import Node
    from app.services.xray import probe_enabled, probe_port_for_node
    if not probe_enabled():
        raise ValueError("Для работы через ноду включите NODE_PROBE_ENABLED на сервере панели")
    node_id = settings.get("node_id")
    node = await db.get(Node, node_id) if node_id else None
    if not node or not node.is_enabled or not node.secret:
        raise ValueError("Выберите включённую ноду с настроенным подключением")
    port = probe_port_for_node(node.id)
    if not 1024 <= port <= 65535:
        raise ValueError("Для выбранной ноды невозможно выделить локальный порт")
    return f"socks5://127.0.0.1:{port}"
