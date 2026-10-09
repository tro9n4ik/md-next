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
        "active_node_id",
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

    notify_node_down_str = settings_db.get("telegram_notify_node_down", "true").lower()
    notify_node_down = notify_node_down_str in ("true", "1", "yes")

    notify_failover_str = settings_db.get("telegram_notify_failover", "true").lower()
    notify_failover = notify_failover_str in ("true", "1", "yes")
    notify_quota = settings_db.get("telegram_notify_quota", "true").lower() in ("true", "1", "yes")

    return {
        "token": token,
        "admin_id": admin_id,
        "proxy_url": "",
        "notify_node_down": notify_node_down,
        "notify_failover": notify_failover,
        "notify_quota": notify_quota,
        "use_node": True,
        "node_id": int(settings_db["active_node_id"]) if settings_db.get("active_node_id", "").isdigit() else None,
    }


async def resolve_telegram_proxy(db, settings: Dict[str, Any], *, active_node=None) -> str:
    """Выбранная нода использует свой SOCKS-вход Xray без изменения маршрутов клиентов."""
    from app.services.xray import probe_enabled, probe_port_for_node
    if not probe_enabled():
        raise ValueError("Для работы через ноду включите NODE_PROBE_ENABLED на сервере панели")
    from app.services.xray import XrayService
    node = active_node if active_node is not None else await XrayService.get_active_node(db)
    if not node or not node.is_enabled or not node.secret:
        raise ValueError("Выберите активную ноду в разделе «Узлы». Telegram работает только через ноду.")
    port = probe_port_for_node(node.id)
    if not 1024 <= port <= 65535:
        raise ValueError("Для выбранной ноды невозможно выделить локальный порт")
    return f"socks5://127.0.0.1:{port}"


async def telegram_node_loop():
    """Follow the committed active node, without a direct fallback."""
    import asyncio
    from app.bot.bot import bot_manager
    while True:
        try:
            settings = await get_telegram_settings_from_db()
            if settings.get("token"):
                async with AsyncSessionLocal() as db:
                    proxy = await resolve_telegram_proxy(db, settings)
                if bot_manager.bot is None:
                    await bot_manager.start(settings["token"], proxy)
                elif getattr(bot_manager, "proxy_url", None) != proxy:
                    await bot_manager.rebind_proxy(proxy)
        except asyncio.CancelledError:
            raise
        except ValueError:
            if bot_manager.bot is not None:
                await bot_manager.stop()
            bot_manager.status = "error"
            bot_manager.last_error = "Нет активной ноды для Telegram. Прямой выход не используется."
        except Exception:
            pass
        await asyncio.sleep(5)
