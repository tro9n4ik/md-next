import logging
import os
import socket
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.node import Node
from app.models.setting import Setting
from app.services.cluster import apply_active_node, get_failover_settings, get_selected_node, is_manual_direct_route
from app.bot.bot import get_bot
from app.bot.handlers import notify_admin
from app.services.events import log_event
from app.services.shell import run_cmd
from app.services.route_probe import probe_current_exit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cluster", tags=["Кластер"])


class ActiveNodeRequest(BaseModel):
    node_id: Optional[int] = None


class FailoverRequest(BaseModel):
    mode: str = Field(pattern="^(manual|auto)$")
    ping_threshold_ms: int = Field(ge=1, le=60000)
    failure_count: int = Field(ge=1, le=20)
    interval_s: int = Field(ge=5, le=3600)
    failback: bool = True
    failback_stable_checks: int = Field(ge=1, le=20)
    cooldown_s: int = Field(ge=0, le=86400)
    fallback_action: str = Field(default="direct", pattern="^(direct|keep)$")


async def _notify(text: str) -> None:
    try:
        await notify_admin(get_bot(), text, notification_type="failover")
    except Exception:
        logger.exception("Не удалось отправить уведомление о кластере")


@router.put("/active-node")
async def set_active_node(
    request: ActiveNodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    node = None
    if request.node_id is not None:
        node = (await db.execute(select(Node).where(Node.id == request.node_id))).scalar_one_or_none()
        if node is None:
            raise HTTPException(status_code=404, detail="Нода не найдена")
        if not node.is_enabled or node.status in {"disabled", "unhealthy", "unavailable"} or not node.is_active:
            raise HTTPException(status_code=409, detail="Нода выключена или недоступна")

    previous = await get_selected_node(db)
    try:
        ok, detail = await apply_active_node(db, node)
        if not ok:
            await db.rollback()
            raise HTTPException(status_code=502, detail=f"Не удалось применить маршрут Xray: {detail}")
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("Не удалось переключить маршрут кластера вручную")
        raise HTTPException(status_code=502, detail=f"Не удалось применить маршрут Xray: {exc}") from exc

    logger.info("Ручное переключение кластера: пользователь=%s, прежний узел=%s, выбранный узел=%s", getattr(current_user, "id", None), getattr(previous, "id", None), getattr(node, "id", None))
    log_event("info", "cluster", "Маршрут Xray переключён вручную", {"previous_node_id": getattr(previous, "id", None), "node_id": getattr(node, "id", None), "actor_id": getattr(current_user, "id", None)})
    destination = f"{node.name} ({node.host})" if node else "прямой выход с мастер-сервера"
    await _notify(f"Маршрут Xray переключён на {destination}.")
    return {"active_node_id": node.id if node else None, "detail": detail}


@router.get("/failover")
async def read_failover(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    return await get_failover_settings(db)


@router.put("/failover")
async def update_failover(request: FailoverRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    values = request.model_dump()
    try:
        for key, value in values.items():
            setting = (await db.execute(select(Setting).where(Setting.key == f"failover.{key}"))).scalar_one_or_none()
            value_str = str(value).lower() if isinstance(value, bool) else str(value)
            if setting is None:
                db.add(Setting(key=f"failover.{key}", value=value_str))
            else:
                setting.value = value_str
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    logger.info("Обновлены настройки резервирования: пользователь=%s, параметры=%s", getattr(current_user, "id", None), values)
    return await get_failover_settings(db)


async def _service_active(service: str) -> bool:
    try:
        if service == "awg":
            code, _, _ = await run_cmd("awg", "show", "awg0", "public-key", timeout=5)
            return code == 0
        if not os.path.exists("/run/systemd/system"):
            return False
        code, _, _ = await run_cmd("systemctl", "is-active", "--quiet", service, timeout=5)
        return code == 0
    except RuntimeError:
        return False


@router.get("/route")
async def get_route(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    nodes = (await db.execute(select(Node).order_by(Node.priority, Node.id))).scalars().all()
    active_node = await get_selected_node(db)
    healthy = [node for node in nodes if node.is_enabled and node.is_active and node.status == "healthy"]
    candidate = next((node for node in healthy if active_node is None or node.id != active_node.id), None)
    failover = await get_failover_settings(db)
    server_name = os.getenv("SERVER_HOST") or socket.gethostname()
    server_ip = os.getenv("SERVER_PUBLIC_IP")
    if not server_ip:
        try:
            server_ip = socket.gethostbyname(server_name)
        except OSError:
            server_ip = "не задан"
    return {
        "server": {"hostname": server_name, "public_ip": server_ip},
        "vpn": {
            "xray": await _service_active("xray"),
            "awg": await _service_active("awg"),
        },
        "active_node": ({"id": active_node.id, "name": active_node.name, "ip": active_node.host, "ping_ms": active_node.ping_ms, "status": active_node.status} if active_node else None),
        "manual_direct": await is_manual_direct_route(db),
        "next_candidate": ({"id": candidate.id, "name": candidate.name, "ip": candidate.host, "ping_ms": candidate.ping_ms, "status": candidate.status} if candidate else None),
        "failover_mode": failover["mode"],
        "fallback_action": failover["fallback_action"],
    }


@router.post("/route/check")
async def check_route(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    """Проверяет реальный внешний IP запросом через SOCKS-вход текущего Xray."""
    active_node = await get_selected_node(db)
    try:
        exit_info = await probe_current_exit()
    except Exception as exc:
        logger.warning("Ошибка проверки маршрута кластера: %s", type(exc).__name__)
        log_event("warning", "cluster", "Не удалось проверить фактический выходной IP Xray", {"error": type(exc).__name__})
        raise HTTPException(status_code=502, detail="Не удалось получить ответ через текущий маршрут Xray. Проверьте подключение ноды и журнал Xray.") from exc

    checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log_event("info", "cluster", "Проверен фактический выходной IP Xray", {"node_id": getattr(active_node, "id", None), "ip": exit_info["ip"]})
    return {
        "active_node": ({"id": active_node.id, "name": active_node.name, "host": active_node.host} if active_node else None),
        "manual_direct": await is_manual_direct_route(db),
        "exit_ip": exit_info["ip"],
        "country": exit_info.get("country"),
        "warp": exit_info.get("warp"),
        "checked_at": checked_at,
    }
