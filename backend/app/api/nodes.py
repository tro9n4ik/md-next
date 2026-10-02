import os
import hashlib
import secrets
import datetime
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update

from app.db.database import get_db
from app.models.node import Node
from app.models.node_invite import NodeInvite
from app.models.setting import Setting
from app.api.auth import get_current_user
from app.services.xray import XrayService
from app.services.cluster import apply_active_node, get_failover_settings, is_manual_direct_route
from app.services.client_service import ClientService
from app.services.events import log_event

router = APIRouter(prefix="/api/v1/nodes", tags=["Nodes"])
logger = logging.getLogger(__name__)

class NodeRegister(BaseModel):
    token: str
    host: str
    port: int
    protocol: str = "trojan"

class NodeResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    protocol: str
    is_active: bool
    is_enabled: bool = True
    status: str = "healthy"
    ping_ms: int
    last_seen: Optional[datetime.datetime] = None
    priority: int = 0

class NodeInviteCreate(BaseModel):
    name: str = "Новый узел"
    ttl_hours: int = 24

class NodeInviteResponse(BaseModel):
    id: int
    name: str
    token: str
    expires_at: datetime.datetime

class NodeInviteItem(BaseModel):
    id: int
    name: str
    created_at: datetime.datetime
    expires_at: datetime.datetime
    used_at: Optional[datetime.datetime] = None
    revoked: bool

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

@router.get("", response_model=List[NodeResponse])
async def get_nodes(db: AsyncSession = Depends(get_db), current_user = Depends(get_current_user)):
    """
    Получить список всех узлов кластера (требует авторизации)
    """
    result = await db.execute(select(Node).order_by(Node.priority, Node.id))
    nodes = result.scalars().all()
    return nodes

class NodeReorder(BaseModel):
    ids: List[int]

@router.post("/reorder")
async def reorder_nodes(data: NodeReorder, db: AsyncSession = Depends(get_db), current_user = Depends(get_current_user)):
    nodes = (await db.execute(select(Node).order_by(Node.id))).scalars().all()
    expected = {node.id for node in nodes}
    if len(data.ids) != len(set(data.ids)) or set(data.ids) != expected:
        raise HTTPException(status_code=422, detail="Передайте все ID нод ровно один раз")
    by_id = {node.id: node for node in nodes}
    for priority, node_id in enumerate(data.ids):
        by_id[node_id].priority = priority
    await db.commit()
    logger.info("cluster.nodes.reordered user=%s ids=%s", getattr(current_user, "id", None), data.ids)
    return {"ids": data.ids}

@router.put("/{node_id}/toggle", response_model=NodeResponse)
async def toggle_node_enabled(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
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

    await db.flush()
    success, reason = await ClientService.sync_xray_clients(db)
    if not success:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Не удалось применить состояние ноды: {reason}")
    await db.commit()
    await db.refresh(node)
    log_event("info", "node", "Узел включён" if node.is_enabled else "Узел отключён", {"node_id": node.id, "name": node.name})
    return node

@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Удалить ноду из кластера и пересобрать конфигурацию Xray (требует авторизации)
    """
    node_res = await db.execute(select(Node).where(Node.id == node_id))
    node = node_res.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Нода не найдена")

    # Сбрасываем node_id у инвайтов, связанных с этой нодой
    await db.execute(
        update(NodeInvite)
        .where(NodeInvite.node_id == node_id)
        .values(node_id=None)
    )

    # Проверяем, была ли эта нода активной нодой каскада
    setting_res = await db.execute(select(Setting).where(Setting.key == "active_node_id"))
    setting = setting_res.scalar_one_or_none()
    is_active_cascade = False
    if setting and setting.value == str(node_id):
        is_active_cascade = True
        setting.value = ""

    await db.delete(node)
    await db.flush()

    # Определение следующей активной ноды для Xray
    next_active_node = None
    if is_active_cascade:
        next_node_res = await db.execute(
            select(Node).where(Node.is_enabled.is_(True), Node.is_active.is_(True), Node.status == "healthy").order_by(Node.priority, Node.id)
        )
        next_active_node = next_node_res.scalars().first()
        if next_active_node and setting:
            setting.value = str(next_active_node.id)
    elif setting and setting.value:
        try:
            curr_id = int(setting.value)
            curr_node_res = await db.execute(select(Node).where(Node.id == curr_id))
            next_active_node = curr_node_res.scalar_one_or_none()
        except ValueError:
            next_active_node = None

    # Загружаем клиентов VLESS для генерации конфига Xray
    success, reason = await ClientService.sync_xray_clients(db, active_node=next_active_node)
    if not success:
        await db.rollback()
        raise HTTPException(status_code=502, detail=f"Ошибка перестройки конфигурации Xray: {reason}")

    await db.commit()
    log_event("info", "node", "Узел удалён", {"node_id": node_id, "name": node.name})
    return None

@router.post("/invites", response_model=NodeInviteResponse)
async def create_node_invite(
    data: NodeInviteCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Создать одноразовое приглашение для подключения новой ноды
    """
    plain_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(plain_token)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=max(1, data.ttl_hours))

    invite = NodeInvite(
        name=data.name,
        token_hash=token_hash,
        expires_at=expires_at
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    log_event("info", "node_invite", "Создано приглашение для нового узла", {"invite_id": invite.id, "name": invite.name})

    return NodeInviteResponse(
        id=invite.id,
        name=invite.name,
        token=plain_token,
        expires_at=invite.expires_at
    )

@router.get("/invites", response_model=List[NodeInviteItem])
async def get_node_invites(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Получить список всех приглашений для нод
    """
    result = await db.execute(select(NodeInvite).order_by(NodeInvite.id.desc()))
    invites = result.scalars().all()
    return invites

@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_node_invite(
    invite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
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

@router.get("/join")
async def join_node_script(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Публичный эндпоинт, отдающий bash-скрипт подключения для валидного одноразового токена
    curl -sSL https://panel.domain.ru/api/v1/nodes/join?token=XYZ | bash
    """
    token_hash = _hash_token(token)
    now = datetime.datetime.now(datetime.timezone.utc)

    result = await db.execute(select(NodeInvite).where(NodeInvite.token_hash == token_hash))
    invite = result.scalar_one_or_none()

    if not invite or invite.revoked or invite.used_at is not None:
        raise HTTPException(status_code=401, detail="Токен подключения недействителен или уже использован")

    exp_at = invite.expires_at
    if exp_at.tzinfo is None:
        exp_at = exp_at.replace(tzinfo=datetime.timezone.utc)

    if exp_at < now:
        raise HTTPException(status_code=401, detail="Срок действия токена подключения истёк")

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

    content = content.replace('PANEL_URL_INJECTED="__PANEL_URL__"', f'PANEL_URL_INJECTED="{panel_url}"')
    content = content.replace('TOKEN_INJECTED="__TOKEN__"', f'TOKEN_INJECTED="{token}"')

    return PlainTextResponse(content, media_type="text/plain")

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_node(node_data: NodeRegister, db: AsyncSession = Depends(get_db)):
    """
    Публичный эндпоинт для регистрации новой ноды из скрипта join-node.sh (по одноразовому токену)
    """
    input_hash = _hash_token(node_data.token)
    now = datetime.datetime.now(datetime.timezone.utc)

    # Атомарное обновление статуса использованности инвайта
    stmt = (
        update(NodeInvite)
        .where(
            NodeInvite.token_hash == input_hash,
            NodeInvite.used_at.is_(None),
            NodeInvite.revoked == False,
            NodeInvite.expires_at > now
        )
        .values(used_at=now)
    )
    res = await db.execute(stmt)
    if res.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Одноразовый токен недействителен, отозван или уже использован")

    invite_res = await db.execute(select(NodeInvite).where(NodeInvite.token_hash == input_hash))
    invite = invite_res.scalar_one()

    node_secret = secrets.token_hex(16)

    node_res = await db.execute(select(Node).where(Node.host == node_data.host))
    target_node = node_res.scalar_one_or_none()

    if target_node:
        target_node.is_active = True
        target_node.port = node_data.port
        target_node.protocol = node_data.protocol
        target_node.secret = node_secret
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
            secret=node_secret,
            is_active=True,
            last_seen=now,
            priority=(last_priority or 0) + (1 if last_priority is not None else 0)
        )
        db.add(target_node)
        await db.flush()
        invite.node_id = target_node.id

    # Проверка режима автопереключения и наличия активной ноды
    failover = await get_failover_settings(db)
    failover_mode = failover["mode"]

    active_node = await XrayService.get_active_node(db)

    if failover_mode == "auto" and active_node is None and not await is_manual_direct_route(db):
        setting_res = await db.execute(select(Setting).where(Setting.key == "active_node_id"))
        setting = setting_res.scalar_one_or_none()
        if setting:
            setting.value = str(target_node.id)
        else:
            db.add(Setting(key="active_node_id", value=str(target_node.id)))

        success, reason = await apply_active_node(db, target_node, source="auto")
        if not success:
            await db.rollback()
            raise HTTPException(status_code=502, detail=f"Ошибка перестройки конфигурации Xray при авто-активации ноды: {reason}")
    else:
        # A re-registration rotates the Trojan password. Every enabled node
        # also needs an outbound/probe even when another node is selected.
        success, reason = await ClientService.sync_xray_clients(db)
        if not success:
            await db.rollback()
            raise HTTPException(status_code=502, detail=f"Ошибка обновления конфигурации ноды: {reason}")

    await db.commit()
    await db.refresh(target_node)
    log_event("info", "node_invite", "Приглашение использовано, узел подключён", {"invite_id": invite.id, "node_id": target_node.id, "name": target_node.name, "host": target_node.host})

    return {"status": "connected", "node_id": target_node.id, "secret": node_secret}
