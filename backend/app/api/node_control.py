"""KedrLink v1: authenticated outbound polling, read-only status command only."""
import datetime as dt
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.db.database import get_db
from app.models.node import Node

router = APIRouter(prefix="/api/v1/nodes", tags=["KedrLink"])
_last_poll: dict[int, float] = {}


def utc(value):
    return value.replace(tzinfo=dt.timezone.utc) if value is not None and value.tzinfo is None else value


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$", max_length=32)
    uptime_ms: int = Field(ge=0, le=2**63-1, strict=True)


class Poll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=1, strict=True)
    snapshot: Snapshot
    completed_command_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Only read-only operations are supported in v1. Never accept shell text.
    kind: str = Field(pattern=r"^status$")


def control_state(node, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    seen = utc(node.control_seen_at)
    enabled = node.transport == "mdnt"
    state = "standard" if not enabled else "unpaired" if not node.control_token_hash else "waiting" if seen is None else "online" if (now-seen).total_seconds() <= 60 else "offline"
    command = None
    if node.control_command_id:
        finished = utc(node.control_command_finished_at)
        deadline = utc(node.control_command_deadline)
        command = {"id": node.control_command_id, "kind": "status", "expires_at": deadline,
                   "status": "completed" if finished else "expired" if deadline <= now else "pending",
                   "result": json.loads(node.control_command_result) if finished and node.control_command_result else None}
    return {"transport": node.transport, "state": state, "seen_at": seen,
            "agent_version": node.control_agent_version, "uptime_ms": node.control_uptime_ms, "command": command}


async def get_node(db, node_id):
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, "Нода не найдена")
    return node


async def authenticate_agent(node_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    if len(token) != 64:
        raise HTTPException(401, "Недействительный ключ агента")
    node = await db.get(Node, node_id)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (node is None or node.transport != "mdnt" or not node.control_token_hash
            or not hmac.compare_digest(digest, node.control_token_hash)):
        raise HTTPException(401, "Недействительный ключ агента")
    return digest


@router.post("/{node_id}/control/poll")
async def poll(node_id: int, data: Poll, digest: str = Depends(authenticate_agent), db: AsyncSession = Depends(get_db)):
    tick = time.monotonic()
    if tick - _last_poll.get(node_id, -100) < 0.5:
        raise HTTPException(429, "Слишком частый опрос", headers={"Retry-After": "1"})
    # Bound process-local bookkeeping; clearing this does not affect authentication.
    if len(_last_poll) >= 4096:
        _last_poll.clear()
    _last_poll[node_id] = tick
    # Authenticate before taking a write lock; recheck after locking so rotation
    # and deletion cannot race with a heartbeat authenticated by an old key.
    await db.rollback()
    await db.execute(text("BEGIN IMMEDIATE"))
    node = await db.get(Node, node_id)
    if node is None or node.transport != "mdnt" or not node.control_token_hash or not hmac.compare_digest(digest, node.control_token_hash):
        raise HTTPException(401, "Недействительный ключ агента")
    now = dt.datetime.now(dt.timezone.utc)
    node.control_seen_at = now
    node.control_agent_version = data.snapshot.agent_version
    node.control_uptime_ms = data.snapshot.uptime_ms
    deadline = utc(node.control_command_deadline)
    if (data.completed_command_id and data.completed_command_id == node.control_command_id
            and not node.control_command_finished_at and deadline and deadline > now):
        node.control_command_result = json.dumps(data.snapshot.model_dump(), separators=(",", ":"))
        node.control_command_finished_at = now
    pending = []
    if node.control_command_id and not node.control_command_finished_at and deadline and deadline > now:
        pending.append({"id": node.control_command_id, "kind": "status", "expires_at_ms": int(deadline.timestamp()*1000)})
    await db.commit()
    return {"version": 1, "poll_after_ms": 15000, "commands": pending}


@router.get("/{node_id}/control", dependencies=[Depends(require_admin)])
async def read_control(node_id: int, db: AsyncSession = Depends(get_db)):
    return control_state(await get_node(db, node_id))


@router.post("/{node_id}/control/commands", dependencies=[Depends(require_admin)], status_code=202)
async def command(node_id: int, data: Command, db: AsyncSession = Depends(get_db)):
    await db.execute(text("BEGIN IMMEDIATE"))
    node = await get_node(db, node_id)
    if node.transport != "mdnt" or not node.control_token_hash:
        raise HTTPException(409, "Эта нода использует стандартный управляющий канал")
    now = dt.datetime.now(dt.timezone.utc)
    if node.control_command_id and not node.control_command_finished_at and utc(node.control_command_deadline) > now:
        # A repeated click reuses the pending read-only request instead of queuing work.
        return control_state(node, now)
    node.control_command_id = secrets.token_hex(16)
    node.control_command_deadline = now + dt.timedelta(seconds=60)
    node.control_command_result = None
    node.control_command_finished_at = None
    await db.commit()
    return control_state(node, now)
