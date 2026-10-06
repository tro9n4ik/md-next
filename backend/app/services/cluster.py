import logging
import asyncio
import os
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.node import Node
from app.models.setting import Setting
from app.models.routing import RoutingRule
from app.services.client_service import ClientService

logger = logging.getLogger(__name__)
_route_lock = asyncio.Lock()

FAILOVER_DEFAULTS = {
    "mode": ("FAILOVER_MODE", "auto"),
    "ping_threshold_ms": ("FAILOVER_PING_THRESHOLD_MS", "300"),
    "failure_count": ("FAILOVER_FAILURE_COUNT", "3"),
    "interval_s": ("FAILOVER_INTERVAL_S", "15"),
    "failback": ("FAILOVER_FAILBACK", "true"),
    "failback_stable_checks": ("FAILOVER_FAILBACK_STABLE_CHECKS", "3"),
    "cooldown_s": ("FAILOVER_COOLDOWN_S", "30"),
    "fallback_action": ("FAILOVER_FALLBACK_ACTION", "direct"),
}


async def get_failover_settings(db: AsyncSession) -> dict:
    rows = (await db.execute(select(Setting).where(Setting.key.like("failover.%")))).scalars().all()
    stored = {row.key.removeprefix("failover."): row.value for row in rows}
    values = {key: stored.get(key, os.getenv(env, default)) for key, (env, default) in FAILOVER_DEFAULTS.items()}
    return {
        "mode": values["mode"] if values["mode"] in {"manual", "auto"} else "manual",
        "ping_threshold_ms": max(1, int(values["ping_threshold_ms"])),
        "failure_count": max(1, int(values["failure_count"])),
        "interval_s": max(5, int(values["interval_s"])),
        "failback": values["failback"].lower() in {"true", "1", "yes", "on"},
        "failback_stable_checks": max(1, int(values["failback_stable_checks"])),
        "cooldown_s": max(0, int(values["cooldown_s"])),
        "fallback_action": values["fallback_action"] if values["fallback_action"] in {"direct", "keep"} else "direct",
    }


async def apply_active_node(db: AsyncSession, node: Optional[Node], *, source: str = "manual") -> tuple[bool, str]:
    """Переключает общий выход и зависимые маршруты одной операцией."""
    async with _route_lock:
        return await _apply_active_node(db, node, source=source)


async def _apply_active_node(db: AsyncSession, node: Optional[Node], *, source: str) -> tuple[bool, str]:
    telegram_proxy = None
    if node is not None:
        warp_node = await db.get(Setting, "warp.node_id")
        warp_usage = await db.get(Setting, "warp.usage")
        if warp_node and warp_node.value:
            from app.services.warp import WarpService
            port = await db.get(Setting, "warp.node_port")
            if warp_usage and warp_usage.value != "off":
                try:
                    trace = await WarpService.test_target(db, {"node_id": node.id, "port": int(port.value) if port else 40000})
                except Exception:
                    return False, "WARP новой ноды недоступен. Прежний общий выход сохранён."
                if trace.get("warp") not in {"on", "plus"} or trace.get("country") == "RU":
                    return False, "На новой ноде нужен подключённый WARP с выходом за пределами России."
                country = await db.get(Setting, "warp.expected_country")
                if country:
                    country.value = trace.get("country", "")
            warp_node.value = str(node.id)
        telegram_use_node = await db.get(Setting, "telegram_use_node")
        if telegram_use_node and telegram_use_node.value.lower() in {"true", "1", "yes"}:
            from app.services.telegram_settings import resolve_telegram_proxy
            try:
                telegram_proxy = await resolve_telegram_proxy(db, {"use_node": True, "node_id": node.id}, active_node=node)
            except ValueError as exc:
                await db.rollback()
                return False, str(exc)
            row = await db.get(Setting, "telegram_node_id")
            if row:
                row.value = str(node.id)
            else:
                db.add(Setting(key="telegram_node_id", value=str(node.id)))
        rules = (await db.execute(select(RoutingRule).where(RoutingRule.action == "proxy"))).scalars().all()
        for rule in rules:
            rule.target_node_id = node.id
        await db.flush()
    ok, detail = await ClientService.sync_xray_clients(db, active_node=node)
    if not ok:
        await db.rollback()
        return False, detail

    result = await db.execute(select(Setting).where(Setting.key == "active_node_id"))
    setting = result.scalar_one_or_none()
    route_value = str(node.id) if node else ("direct:manual" if source == "manual" else "direct:auto")
    if setting is None:
        db.add(Setting(key="active_node_id", value=route_value))
    else:
        setting.value = route_value
    await db.flush()
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise
    if telegram_proxy is not None:
        from app.bot.bot import bot_manager
        await bot_manager.rebind_proxy(telegram_proxy)
    logger.info("Изменён маршрут кластера: узел=%s", node.id if node else None)
    return True, detail


async def get_selected_node(db: AsyncSession) -> Optional[Node]:
    result = await db.execute(select(Setting).where(Setting.key == "active_node_id"))
    setting = result.scalar_one_or_none()
    if not setting or not setting.value:
        return None
    try:
        return (await db.execute(select(Node).where(Node.id == int(setting.value)))).scalar_one_or_none()
    except (TypeError, ValueError):
        return None


async def is_manual_direct_route(db: AsyncSession) -> bool:
    result = await db.execute(select(Setting).where(Setting.key == "active_node_id"))
    setting = result.scalar_one_or_none()
    return bool(setting and setting.value == "direct:manual")
