import asyncio
import os
import hashlib
import secrets
from uuid import UUID
import datetime
import logging
import shlex
import time
from collections import deque
from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from app.services.input_validation import validate_host
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, delete, or_, insert, func, literal, text

from app.db.database import get_db
from app.models.node import Node
from app.models.node_invite import NodeInvite
from app.models.setting import Setting
from app.api.auth import get_current_user, require_admin
from app.schemas.node import NodePublicResponse, NodeAdminResponse
from app.services.xray import XrayService
from app.services.cluster import get_failover_settings, is_manual_direct_route
from app.services.client_service import ClientService
from app.services.events import log_event, log_node_event
from app.services.request_ip import request_ip

router = APIRouter(prefix="/api/v1/nodes", tags=["Узлы"])
logger = logging.getLogger(__name__)
NodeTransport = Literal["xray", "mdnt"]

NODE_REGISTRATION_WINDOW = 60
NODE_REGISTRATION_IP_LIMIT = 20
NODE_REGISTRATION_FAILURE_LIMIT = 120
NODE_REGISTRATION_LOG_THRESHOLD = 3
NODE_REGISTRATION_MIN_FAILURE_TIME = 0.05
NODE_PENDING_INVITE_LIMIT = 100
INVALID_NODE_TOKEN = "Токен подключения недействителен"

_registration_window = -1
_registration_attempts: dict[str, int] = {}
_registration_ip_failures: dict[str, int] = {}
_registration_failures: deque[float] = deque()
_registration_pending = 0


async def limit_node_registration(request: Request):
    global _registration_window, _registration_pending
    started = time.perf_counter()
    window = int(started // NODE_REGISTRATION_WINDOW)
    if window != _registration_window:
        _registration_attempts.clear()
        _registration_ip_failures.clear()
        _registration_window = window
    while _registration_failures and _registration_failures[0] <= started - NODE_REGISTRATION_WINDOW:
        _registration_failures.popleft()

    ip = request_ip(request)
    attempts = _registration_attempts.get(ip, 0)
    if (attempts >= NODE_REGISTRATION_IP_LIMIT
            or len(_registration_failures) + _registration_pending >= NODE_REGISTRATION_FAILURE_LIMIT):
        raise HTTPException(status_code=429, detail="Слишком много запросов")

    _registration_attempts[ip] = attempts + 1
    _registration_pending += 1
    try:
        try:
            yield
        finally:
            _registration_pending -= 1
    except HTTPException as exc:
        if exc.status_code == 401:
            _registration_failures.append(time.perf_counter())
            failures = _registration_ip_failures.get(ip, 0) + 1
            _registration_ip_failures[ip] = failures
            if failures == NODE_REGISTRATION_LOG_THRESHOLD:
                log_event("warning", "node_invite", "Повторные неудачные проверки токена подключения",
                          {"attempts": failures})
            deadline = started + NODE_REGISTRATION_MIN_FAILURE_TIME
            while (remaining := deadline - time.perf_counter()) > 0:
                await asyncio.sleep(remaining)
        raise


def active_invite_conditions(now):
    return (
        NodeInvite.used_at.is_(None),
        NodeInvite.revoked.is_(False),
        NodeInvite.expires_at > now,
    )


class NodeRegister(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    protocol: Literal['trojan', 'vless'] = "trojan"
    transport: NodeTransport = "xray"
    control_protocol: Literal[1] | None = None
    public_key: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{43}$')
    short_id: str = Field(default='', pattern=r'^(?:[0-9a-f]{2}){0,8}$')
    identity: UUID | None = None

    @model_validator(mode='after')
    def secure_transport_fields(self):
        if self.transport == "mdnt" and self.control_protocol != 1:
            raise ValueError("Для KedrLink необходим управляющий протокол версии 1")
        if self.protocol == 'vless' and (not self.public_key or not self.identity):
            raise ValueError('Для VLESS Reality необходимы публичный ключ и UUID')
        if self.protocol == 'trojan' and (self.public_key or self.identity or self.short_id):
            raise ValueError('Параметры Reality допустимы только для VLESS')
        return self

    @field_validator('host')
    @classmethod
    def valid_host(cls, value: str) -> str:
        return validate_host(value)

NodeResponse = NodeAdminResponse


def node_response(node, user):
    model = NodeAdminResponse if user.role == "admin" else NodePublicResponse
    values = {key: getattr(node, key) for key in (
        "id", "name", "country_code", "status", "last_seen",
        "host", "port", "protocol", "is_active", "is_enabled", "ping_ms", "priority",
        "transport",
    )}
    values["is_online"] = bool(node.is_enabled and node.is_active and node.status == "healthy")
    return model.model_validate(values)

class NodeInviteCreate(BaseModel):
    name: str = "Новый узел"
    ttl_hours: int = 24
    transport: NodeTransport = "xray"

class NodeInviteResponse(BaseModel):
    id: int
    name: str
    token: str
    transport: NodeTransport = "xray"
    expires_at: datetime.datetime

class NodeInviteItem(BaseModel):
    id: int
    name: str
    transport: NodeTransport = "xray"
    created_at: datetime.datetime
    expires_at: datetime.datetime
    used_at: Optional[datetime.datetime] = None
    revoked: bool

    @field_validator('created_at', 'expires_at', 'used_at')
    @classmethod
    def utc_timestamps(cls, value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
        # SQLite возвращает время без зоны; приглашения хранятся в UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

@router.get("", response_model=list[NodeAdminResponse | NodePublicResponse])
async def get_nodes(db: AsyncSession = Depends(get_db), current_user = Depends(get_current_user)):
    """
    Получить список всех узлов кластера (требует авторизации)
    """
    result = await db.execute(select(Node).order_by(Node.priority, Node.id))
    nodes = result.scalars().all()
    return [node_response(node, current_user) for node in nodes]

class NodeReorder(BaseModel):
    ids: List[int]

@router.post("/reorder")
async def reorder_nodes(data: NodeReorder, db: AsyncSession = Depends(get_db), current_user = Depends(require_admin)):
    from app.services.cluster import _route_lock
    async with _route_lock:
        return await _reorder_nodes(data, db, current_user)


async def _reorder_nodes(data, db, current_user):
    nodes = (await db.execute(select(Node).order_by(Node.id))).scalars().all()
    expected = {node.id for node in nodes}
    if len(data.ids) != len(set(data.ids)) or set(data.ids) != expected:
        raise HTTPException(status_code=422, detail="Передайте все ID нод ровно один раз")
    by_id = {node.id: node for node in nodes}
    for priority, node_id in enumerate(data.ids):
        by_id[node_id].priority = priority
    await db.commit()
    logger.info("Изменён порядок узлов: пользователь=%s, идентификаторы=%s", getattr(current_user, "id", None), data.ids)
    return {"ids": data.ids}

@router.put("/{node_id}/toggle", response_model=NodeResponse)
async def toggle_node_enabled(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_admin)
):
    from app.services.cluster import _route_lock
    async with _route_lock:
        return await _toggle_node_enabled(node_id, db, current_user)


async def _toggle_node_enabled(node_id, db, current_user):
    """
    Включить или отключить узел кластера вручную (администратором)
    """
    res = await db.execute(select(Node).where(Node.id == node_id))
    node = res.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    node.is_enabled = not getattr(node, 'is_enabled', True)
    if not node.is_enabled:
        node.is_active = False
        node.status = "disabled"
    else:
        node.status = "healthy"
        node.is_active = True

    try:
        await db.flush()
        success, reason = await ClientService.sync_xray_clients(db)
        if not success:
            raise HTTPException(status_code=502, detail=f"Не удалось применить состояние ноды: {reason}")
        await db.commit()
    except Exception:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise
    await db.refresh(node)
    log_node_event("info", "Узел включён" if node.is_enabled else "Узел отключён", node)
    return node_response(node, current_user)

@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_admin)
):
    from app.services.cluster import _route_lock
    async with _route_lock:
        return await _delete_node(node_id, db, current_user)


async def _delete_node(node_id, db, current_user):
    """
    Удалить ноду из кластера и пересобрать конфигурацию Xray (требует авторизации)
    """
    # Регистрация тоже меняет список нод. Фиксируем порядок до чтения зависимостей.
    await db.execute(text("BEGIN IMMEDIATE"))
    node_res = await db.execute(select(Node).where(Node.id == node_id))
    node = node_res.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    from app.models.client import Client
    from app.models.routing import RoutingRule
    from app.services.client_exit import selectable
    linked_rules = (await db.execute(
        select(RoutingRule.id).where(
            RoutingRule.target_node_id == node_id,
            RoutingRule.action == "proxy",
        ).order_by(RoutingRule.id)
    )).scalars().all()
    if linked_rules:
        rule_ids = ", ".join(f"№{rule_id}" for rule_id in linked_rules[:10])
        raise HTTPException(409, detail=(
            f"Нода используется в правилах маршрутизации: {rule_ids} "
            f"(всего {len(linked_rules)}). Сначала выберите другую ноду или удалите эти правила "
            "в разделе «Маршрутизация». Отключённые правила также сохраняют привязку."
        ))

    setting = await db.get(Setting, "active_node_id")
    remaining = (await db.execute(select(Node).where(Node.id != node_id).order_by(Node.priority, Node.id))).scalars().all()
    next_active_node = None
    if setting and setting.value == str(node_id):
        next_active_node = next((candidate for candidate in remaining
                                 if candidate.is_active and candidate.status == "healthy"
                                 and selectable(candidate)), None)
        if next_active_node is None and (await get_failover_settings(db))["fallback_action"] == "keep":
            raise HTTPException(409, detail="Сначала выберите доступную ноду. Настройки запрещают автоматический прямой выход.")
        setting.value = str(next_active_node.id) if next_active_node else "direct:auto"
    elif setting and setting.value.isdigit():
        next_active_node = next((candidate for candidate in remaining if candidate.id == int(setting.value)), None)

    await db.execute(update(Client).where(Client.exit_node_id == node_id).values(exit_node_id=None))
    await db.execute(
        update(NodeInvite)
        .where(NodeInvite.node_id == node_id)
        .values(node_id=None)
    )
    await db.execute(update(RoutingRule).where(
        RoutingRule.target_node_id == node_id, RoutingRule.action != "proxy",
    ).values(target_node_id=None))
    await db.execute(update(Setting).where(
        Setting.key.in_(("warp.node_id", "telegram_node_id")), Setting.value == str(node_id),
    ).values(value=str(next_active_node.id) if next_active_node else ""))
    for priority, candidate in enumerate(remaining):
        candidate.priority = priority

    await db.delete(node)
    await db.flush()

    try:
        success, reason = await ClientService.sync_xray_clients(db, active_node=next_active_node)
        if not success:
            raise HTTPException(status_code=502, detail=f"Ошибка перестройки конфигурации Xray: {reason}")
        await db.commit()
    except Exception:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise

    log_node_event("info", "Узел удалён", node)
    return None

@router.post("/invites", response_model=NodeInviteResponse)
async def create_node_invite(
    data: NodeInviteCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_admin)
):
    """
    Создать одноразовое приглашение для подключения новой ноды
    """
    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(hours=max(1, data.ttl_hours))
    pending = select(func.count()).select_from(NodeInvite).where(*active_invite_conditions(now)).scalar_subquery()
    statement = insert(NodeInvite).from_select(
        ["name", "token_hash", "expires_at", "revoked", "transport"],
        select(literal(data.name), literal(token_hash), literal(expires_at), literal(False), literal(data.transport))
        .where(pending < NODE_PENDING_INVITE_LIMIT),
    ).returning(NodeInvite)
    invite = (await db.execute(statement)).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=429, detail="Достигнут лимит действующих приглашений")
    await db.commit()
    log_event("info", "node_invite", "Создано приглашение для нового узла", {"invite_id": invite.id, "name": invite.name})

    return NodeInviteResponse(
        id=invite.id,
        name=invite.name,
        token=plain_token,
        transport=invite.transport,
        expires_at=invite.expires_at
    )

@router.get("/invites", response_model=List[NodeInviteItem])
async def get_node_invites(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_admin)
):
    """
    Получить список всех приглашений для нод
    """
    result = await db.execute(select(NodeInvite).order_by(NodeInvite.id.desc()))
    invites = result.scalars().all()
    return invites

@router.delete("/invites/history")
async def clear_node_invite_history(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_admin),
):
    """Удаляет только использованные, отозванные и истёкшие приглашения."""
    now = datetime.datetime.now(datetime.timezone.utc)
    result = await db.execute(delete(NodeInvite).where(or_(
        NodeInvite.used_at.is_not(None),
        NodeInvite.revoked.is_(True),
        NodeInvite.expires_at <= now,
    )))
    deleted = result.rowcount
    await db.commit()
    if deleted:
        log_event("info", "node_invite", "Очищена история приглашений узлов", {"deleted": deleted, "actor_id": current_user.id})
    return {"deleted": deleted}


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_node_invite(
    invite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_admin)
):
    """
    Отозвать одноразовое приглашение по ID
    """
    result = await db.execute(select(NodeInvite).where(NodeInvite.id == invite_id))
    invite = result.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")

    invite.revoked = True
    await db.commit()
    log_event("info", "node_invite", "Приглашение для узла отозвано", {"invite_id": invite.id, "name": invite.name})
    return None

@router.get("/join", dependencies=[Depends(limit_node_registration)])
async def join_node_script(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Публичный эндпоинт, отдающий bash-скрипт подключения для валидного одноразового токена
    curl -sSL https://panel.domain.ru/api/v1/nodes/join?token=XYZ | bash
    """
    token_hash = _hash_token(token)
    now = datetime.datetime.now(datetime.timezone.utc)

    result = await db.execute(select(NodeInvite).where(
        NodeInvite.token_hash == token_hash, *active_invite_conditions(now),
    ))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=401, detail=INVALID_NODE_TOKEN)


    panel_url = os.getenv("PANEL_PUBLIC_URL")
    if not panel_url:
        panel_url = str(request.base_url).rstrip('/')
        if panel_url.startswith("http://"):
            panel_url = "https://" + panel_url[7:]

    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../scripts/join-node.sh"))

    if not os.path.exists(script_path):
        raise HTTPException(status_code=500, detail="Скрипт join-node.sh не найден")

    with open(script_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace('PANEL_URL_INJECTED="__PANEL_URL__"', f'PANEL_URL_INJECTED={shlex.quote(panel_url)}')
    content = content.replace('TOKEN_INJECTED="__TOKEN__"', f'TOKEN_INJECTED={shlex.quote(token)}')
    content = content.replace('TRANSPORT_INJECTED="__TRANSPORT__"', f'TRANSPORT_INJECTED={shlex.quote(invite.transport)}')
    import base64
    control_path = os.path.join(os.path.dirname(script_path), "warp-node-control.py")
    with open(control_path, "rb") as control_source:
        content = content.replace("__WARP_CONTROL_SOURCE__", base64.b64encode(control_source.read()).decode())
    if invite.transport == "mdnt":
        agent_path = os.path.join(os.path.dirname(script_path), "../experimental/node-transport/cmd/kedrlink-agent/main.go")
        if not os.path.isfile(agent_path):
            raise HTTPException(503, "Исходный код KedrLink отсутствует в этой сборке панели")
        with open(agent_path, "rb") as agent_source:
            content = content.replace("__KEDRLINK_SOURCE__", base64.b64encode(agent_source.read()).decode())

    return PlainTextResponse(content, media_type="text/plain")

@router.post("/register", status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_node_registration)])
async def register_node(node_data: NodeRegister, db: AsyncSession = Depends(get_db)):
    """
    Публичный эндпоинт для регистрации новой ноды из скрипта join-node.sh (по одноразовому токену)
    """
    from app.services.cluster import _route_lock
    async with _route_lock:
        return await _register_node(node_data, db)


async def _register_node(node_data, db):
    input_hash = _hash_token(node_data.token)
    now = datetime.datetime.now(datetime.timezone.utc)

    # Атомарное обновление статуса использованности инвайта
    stmt = (
        update(NodeInvite)
        .where(
            NodeInvite.token_hash == input_hash,
            *active_invite_conditions(now),
        )
        .values(used_at=now)
    )
    res = await db.execute(stmt)
    if res.rowcount == 0:
        await db.rollback()
        raise HTTPException(status_code=401, detail=INVALID_NODE_TOKEN)

    invite_res = await db.execute(select(NodeInvite).where(NodeInvite.token_hash == input_hash))
    invite = invite_res.scalar_one()

    if node_data.transport != invite.transport:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Транспорт ноды не совпадает с приглашением")

    node_secret = str(node_data.identity) if node_data.protocol == "vless" else secrets.token_hex(16)

    node_res = await db.execute(select(Node).where(Node.host == node_data.host))
    target_node = node_res.scalar_one_or_none()

    if target_node:
        target_node.is_active = True
        target_node.port = node_data.port
        target_node.protocol = node_data.protocol
        target_node.transport = invite.transport
        target_node.secret = node_secret
        target_node.public_key = node_data.public_key
        target_node.short_id = node_data.short_id
        target_node.last_seen = now
        invite.node_id = target_node.id
    else:
        node_name = invite.name if invite.name != "Новый узел" else f"node-{node_data.host}"
        last_priority = (await db.execute(select(Node.priority).order_by(Node.priority.desc()).limit(1))).scalar_one_or_none()
        target_node = Node(
            name=node_name,
            host=node_data.host,
            port=node_data.port,
            protocol=node_data.protocol,
            transport=invite.transport,
            secret=node_secret,
            public_key=node_data.public_key,
            short_id=node_data.short_id,
            is_active=True,
            last_seen=now,
            priority=(last_priority or 0) + (1 if last_priority is not None else 0)
        )
        db.add(target_node)
        await db.flush()
        invite.node_id = target_node.id

    # The management identity is separate from the Xray data-plane credential.
    control_token = secrets.token_hex(32) if invite.transport == "mdnt" else None
    target_node.control_token_hash = _hash_token(control_token) if control_token else None
    target_node.control_seen_at = None
    target_node.control_agent_version = None
    target_node.control_uptime_ms = None
    target_node.control_command_id = None
    target_node.control_command_deadline = None
    target_node.control_command_result = None
    target_node.control_command_finished_at = None

    # Проверка режима автопереключения и наличия активной ноды
    failover = await get_failover_settings(db)
    failover_mode = failover["mode"]

    active_node = await XrayService.get_active_node(db)

    if failover_mode == "auto" and active_node is None and not await is_manual_direct_route(db):
        # The route lock owns creation/update of active_node_id. Creating it
        # before acquiring that lock races with a simultaneous registration.
        # register_node already owns the route lock, before its first DB write.
        from app.services.cluster import _apply_active_node
        success, reason = await _apply_active_node(db, target_node, source="auto")
        if not success:
            await db.rollback()
            raise HTTPException(status_code=502, detail=f"Ошибка перестройки конфигурации Xray при авто-активации ноды: {reason}")
    else:
        # Повторная регистрация меняет пароль Trojan. Каждому включённому узлу
        # нужен исходящий маршрут и проверка, даже если выбран другой узел.
        success, reason = await ClientService.sync_xray_clients(db)
        if not success:
            await db.rollback()
            raise HTTPException(status_code=502, detail=f"Ошибка обновления конфигурации ноды: {reason}")

    await db.commit()
    await db.refresh(target_node)
    log_event("info", "node_invite", "Приглашение использовано, узел подключён", {"invite_id": invite.id, "node_id": target_node.id, "name": target_node.name, "host": target_node.host})

    response = {"status": "connected", "node_id": target_node.id, "secret": node_secret, "transport": target_node.transport}
    if control_token:
        response["control"] = {"protocol": 1, "token": control_token}
    return response


@router.get("/{node_id}", response_model=NodeAdminResponse | NodePublicResponse)
async def read_node(node_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, detail="Нода не найдена")
    return node_response(node, user)
