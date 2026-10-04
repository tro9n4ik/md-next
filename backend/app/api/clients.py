import base64
import logging
import os
import uuid
import secrets
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.client import Client, ClientProfile
from app.services.awg import AWGService
from app.services.client_service import ClientService
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.profiles import make_profile_data, get_profile_settings, create_profiles, enabled_profile_kinds
from app.services.cdn import make_cdn_link
from app.services.events import log_event
from app.models.setting import Setting
from app.services.happ_routing import build_happ_routing_link
from app.services.client_limits import access_allowed, expiry_for_period, limit_info, refresh_period, utc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients", tags=["Клиенты"], dependencies=[Depends(get_current_user)])
subscription_router = APIRouter(prefix="/api/v1/sub", tags=["Подписки"])
SUBSCRIPTION_REQUESTS: dict[str, list[float]] = {}
SUBSCRIPTION_LIMIT = 120
SUBSCRIPTION_WINDOW = 60


class ClientCreate(BaseModel):
    name: str
    phone: str | None = None
    email: str | None = None
    protocol: str | None = None
    subscription_period: Literal["week", "month", "year", "custom", "unlimited"] = "unlimited"
    expires_at: datetime | None = None
    monthly_traffic_limit: int = Field(default=0, ge=0, le=9007199254740991)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 64:
            raise ValueError("Имя должно содержать от 1 до 64 символов")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str:
        value = (value or "").strip()
        if value and ("@" not in value or "." not in value.rsplit("@", 1)[-1]):
            raise ValueError("Некорректный формат email")
        return value


class ClientUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    is_active: bool | None = None
    subscription_period: Literal["week", "month", "year", "custom", "unlimited"] | None = None
    expires_at: datetime | None = None
    monthly_traffic_limit: int | None = Field(default=None, ge=0, le=9007199254740991)


class TrafficUpdate(BaseModel):
    bytes_used: int


async def _sync_protocols(db: AsyncSession) -> None:
    ok, message = await ClientService.sync_xray_clients(db)
    if not ok:
        raise RuntimeError(message)
    ok, message = await AWGService.sync_server_config(db)
    if not ok:
        raise RuntimeError(message)


def _client_sort_key(client: Client, sort: str, profiles: list[ClientProfile]):
    if sort == "status":
        return int(access_allowed(client))
    if sort == "traffic":
        return int(client.traffic_total or client.traffic_used or 0)
    if sort == "protocol":
        return ",".join(sorted(p.kind for p in profiles if p.is_enabled))
    if sort == "created":
        return client.created_at.isoformat() if client.created_at else ""
    return client.name.casefold()


@router.get("")
async def get_clients(
    q: str = "", status_filter: Literal["all", "active", "disabled"] | None = None,
    sort: Literal["name", "status", "traffic", "protocol", "created"] = "created",
    order: Literal["asc", "desc"] = "desc", status: Literal["all", "active", "disabled"] | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Client)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(or_(Client.name.ilike(pattern), Client.phone.ilike(pattern), Client.email.ilike(pattern)))
    filter_status = status or status_filter or "all"
    clients = list((await db.execute(query)).scalars().all())
    now = datetime.now(timezone.utc)
    if filter_status != "all":
        clients = [client for client in clients if access_allowed(client, now) == (filter_status == "active")]
    profiles_result = await db.execute(select(ClientProfile).where(ClientProfile.client_id.in_([c.id for c in clients]))) if clients else None
    profiles_by_client: dict[int, list[ClientProfile]] = {}
    globally_enabled = await enabled_profile_kinds(db)
    for profile in profiles_result.scalars().all() if profiles_result else []:
        if profile.kind in globally_enabled:
            profiles_by_client.setdefault(profile.client_id, []).append(profile)
    clients.sort(key=lambda c: _client_sort_key(c, sort, profiles_by_client.get(c.id, [])), reverse=order == "desc")
    return [
        {
            "id": c.id, "name": c.name, "phone": c.phone, "email": c.email,
            "protocol": c.protocol, "traffic_used": c.traffic_total or c.traffic_used or 0,
            "traffic_total": c.traffic_total or c.traffic_used or 0, "traffic_limit": c.traffic_limit or 0,
            "traffic_up": sum(p.traffic_up or 0 for p in profiles_by_client.get(c.id, [])),
            "traffic_down": sum(p.traffic_down or 0 for p in profiles_by_client.get(c.id, [])),
            "is_active": c.is_active, "created_at": c.created_at,
            **limit_info(c, now),
            "profiles": [{"id": p.id, "kind": p.kind, "is_enabled": p.is_enabled} for p in profiles_by_client.get(c.id, [])],
        }
        for c in clients
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_client(client_data: ClientCreate, db: AsyncSession = Depends(get_db)):
    enabled = await enabled_profile_kinds(db)
    protocol_settings = await get_profile_settings(db)
    if client_data.protocol == "vless" and any(kind.startswith("vless_") for kind in enabled) and not all(protocol_settings.get(key) for key in ("protocol.reality.server_address", "protocol.reality.public_key", "protocol.reality.server_name")):
        raise HTTPException(status_code=503, detail="Сервер не настроен для создания VLESS-профилей")
    now = datetime.now(timezone.utc)
    try:
        expires = expiry_for_period(client_data.subscription_period, client_data.expires_at, now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    client = Client(name=client_data.name.strip(), phone=client_data.phone or "", email=client_data.email or "", protocol=None,
                    created_at=now, expires_at=expires, monthly_traffic_limit=client_data.monthly_traffic_limit, traffic_period_start=now)
    db.add(client)
    try:
        await db.flush()
        profiles = await create_profiles(db, client)
        await _sync_protocols(db)
        settings = await get_profile_settings(db)
        legacy_link = next((make_profile_data(client, p, settings) for p in profiles if p.kind == "vless_reality_tcp"), None)
        legacy_conf = next((make_profile_data(client, p, settings) for p in profiles if p.kind == "awg"), None)
        legacy_protocol = client_data.protocol or ("vless" if legacy_link else "awg" if legacy_conf else None)
        await db.commit()
        log_event("info", "client", "Клиент создан", {"client_id": client.id, "name": client.name})
        return {
            "client": {"id": client.id, "name": client.name, "phone": client.phone, "email": client.email, "protocol": legacy_protocol,
                       **limit_info(client),
                       "uuid": next((p.uuid for p in profiles if p.kind == "vless_reality_tcp"), None)},
            "profiles": [{"id": p.id, "kind": p.kind} for p in profiles],
            "link": legacy_link, "conf": legacy_conf,
        }
    except Exception as exc:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        logger.exception("Не удалось создать клиента")
        detail = str(exc) or exc.__class__.__name__
        raise HTTPException(status_code=502, detail=f"Не удалось создать и применить профили клиента: {detail}") from exc


@router.get("/{client_id}/profiles")
async def get_client_profiles(client_id: int, db: AsyncSession = Depends(get_db)):
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    result = await db.execute(select(ClientProfile).where(ClientProfile.client_id == client_id).order_by(ClientProfile.id))
    globally_enabled = await enabled_profile_kinds(db)
    settings = await get_profile_settings(db)
    return {
        "client": {"id": client.id, "name": client.name, "is_active": client.is_active, **limit_info(client)},
        "profiles": [
            {"id": p.id, "kind": p.kind, "label": p.kind.replace("_", " ").upper(), "is_enabled": p.is_enabled,
             "data": make_profile_data(client, p, settings), "key_available": bool(decrypt_secret(p.private_key_enc or "")) if p.kind == "awg" else True}
            for p in result.scalars().all() if p.kind in globally_enabled
        ],
        "subscription_url": f"{os.getenv('PANEL_PUBLIC_URL', '').rstrip('/')}/sub/{client.sub_token}",
    }


@router.put("/{client_id}/profiles/{profile_id}")
async def update_client_profile(client_id: int, profile_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    profile = await db.get(ClientProfile, profile_id)
    if profile is None or profile.client_id != client_id:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    previous = profile.is_enabled
    profile.is_enabled = bool(data.get("is_enabled", previous))
    try:
        await _sync_protocols(db)
        await db.commit()
        log_event("info", "profile", "Профиль клиента включён" if profile.is_enabled else "Профиль клиента отключён", {"client_id": client_id, "profile_id": profile_id, "kind": profile.kind})
    except Exception as exc:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise HTTPException(status_code=502, detail=f"Не удалось применить профиль: {exc}") from exc
    return {"id": profile.id, "is_enabled": profile.is_enabled}


@router.post("/{client_id}/profiles/{profile_id}/regenerate")
async def regenerate_profile(client_id: int, profile_id: int, db: AsyncSession = Depends(get_db)):
    profile = await db.get(ClientProfile, profile_id)
    if profile is None or profile.client_id != client_id:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    if profile.kind.startswith("vless_"):
        profile.uuid = str(uuid.uuid4())
    elif profile.kind == "hysteria2":
        profile.auth = secrets.token_urlsafe(24)
    elif profile.kind == "awg":
        private_key, profile.public_key = AWGService.generate_keypair()
        profile.private_key_enc = encrypt_secret(private_key)
    try:
        await _sync_protocols(db)
        await db.commit()
        log_event("info", "profile", "Профиль клиента перевыпущен", {"client_id": client_id, "profile_id": profile_id, "kind": profile.kind})
    except Exception as exc:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise HTTPException(status_code=502, detail=f"Не удалось перевыпустить профиль: {exc}") from exc
    return {"status": "ok"}


@router.post("/{client_id}/sub/regenerate")
async def regenerate_subscription(client_id: int, db: AsyncSession = Depends(get_db)):
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    client.sub_token = secrets.token_urlsafe(32)
    await db.commit()
    log_event("info", "client", "Ссылка подписки клиента перевыпущена", {"client_id": client_id})
    return {"subscription_url": f"{os.getenv('PANEL_PUBLIC_URL', '').rstrip('/')}/sub/{client.sub_token}"}


@subscription_router.get("/{token}", response_class=Response)
async def get_subscription(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    now = time.time()
    ip = request.headers.get("X-Real-IP") or (request.client.host if request.client else "unknown")
    attempts = [stamp for stamp in SUBSCRIPTION_REQUESTS.get(ip, []) if now - stamp < SUBSCRIPTION_WINDOW]
    if len(attempts) >= SUBSCRIPTION_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    attempts.append(now)
    SUBSCRIPTION_REQUESTS[ip] = attempts
    client = (await db.execute(select(Client).where(Client.sub_token == token))).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    result = await db.execute(select(ClientProfile).where(ClientProfile.client_id == client.id, ClientProfile.is_enabled.is_(True)))
    globally_enabled = await enabled_profile_kinds(db)
    settings = await get_profile_settings(db)
    links = []
    if access_allowed(client):
        for profile in result.scalars().all():
            if profile.kind not in globally_enabled:
                continue
            if profile.kind == "awg":
                continue
            link = make_profile_data(client, profile, settings)
            if link:
                links.append(link)
            cdn_link = make_cdn_link(client, profile, settings)
            if cdn_link:
                links.append(cdn_link)
    content = base64.b64encode("\n".join(links).encode("utf-8")).decode("ascii")
    dns_rows = (await db.execute(select(Setting).where(Setting.key.like("dns.%")))).scalars().all()
    happ_dns = build_happ_routing_link({row.key: row.value for row in dns_rows})
    title = client.name if client.name.isascii() else base64.b64encode(client.name.encode("utf-8")).decode("ascii")
    totals = await db.execute(select(ClientProfile.traffic_up, ClientProfile.traffic_down).where(ClientProfile.client_id == client.id))
    usage = list(totals.all())
    upload = sum(up or 0 for up, _ in usage)
    download = sum(down or 0 for _, down in usage)
    limits = limit_info(client)
    # Старый суммарный лимит сохраняет прежнее поведение для существующих клиентов.
    total = client.traffic_limit or 0
    if client.monthly_traffic_limit:
        upload, download = limits["monthly_traffic_up"], limits["monthly_traffic_down"]
        total = limits["monthly_traffic_limit"]
    userinfo = f"upload={upload}; download={download}; total={total}"
    if client.expires_at:
        userinfo += f"; expire={int(utc(client.expires_at).timestamp())}"
    return Response(content=content, media_type="text/plain", headers={
        "profile-title": title,
        "Subscription-Userinfo": userinfo,
        "profile-update-interval": "12",
        "routing": happ_dns,
    })


@router.put("/{client_id}")
async def update_client(client_id: int, data: ClientUpdate, db: AsyncSession = Depends(get_db)):
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    was_active = client.is_active
    now = datetime.now(timezone.utc)
    try:
        if data.subscription_period is not None:
            client.expires_at = expiry_for_period(data.subscription_period, data.expires_at, now, client.expires_at)
        elif "expires_at" in data.model_fields_set:
            client.expires_at = expiry_for_period("custom" if data.expires_at else "unlimited", data.expires_at, now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if data.monthly_traffic_limit is not None:
        client.monthly_traffic_limit = data.monthly_traffic_limit
    refresh_period(client, now)
    for field in ("name", "phone", "email", "is_active"):
        value = getattr(data, field)
        if value is not None:
            setattr(client, field, value)
    try:
        await _sync_protocols(db)
        await db.commit()
        if was_active != client.is_active:
            log_event("info", "client", "Клиент включён" if client.is_active else "Клиент отключён", {"client_id": client.id, "name": client.name})
        return {"id": client.id, "name": client.name, "is_active": client.is_active, **limit_info(client)}
    except Exception as exc:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise HTTPException(status_code=502, detail=f"Не удалось обновить клиента: {exc}") from exc


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(client_id: int, db: AsyncSession = Depends(get_db)):
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    deleted_name = client.name
    await db.delete(client)
    await db.flush()
    try:
        await _sync_protocols(db)
        await db.commit()
        log_event("info", "client", "Клиент удалён", {"client_id": client_id, "name": deleted_name})
    except Exception as exc:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise HTTPException(status_code=502, detail=f"Не удалось удалить клиента из протоколов: {exc}") from exc


@router.post("/{client_id}/traffic", deprecated=True)
async def record_client_traffic(client_id: int, data: TrafficUpdate, db: AsyncSession = Depends(get_db)):
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    client.traffic_total = max(client.traffic_total or 0, client.traffic_used or 0) + max(0, data.bytes_used)
    client.traffic_used = client.traffic_total
    refresh_period(client)
    client.monthly_traffic_down = (client.monthly_traffic_down or 0) + max(0, data.bytes_used)
    if client.traffic_limit and client.traffic_total >= client.traffic_limit:
        client.is_active = False
    if not access_allowed(client):
        try:
            await _sync_protocols(db)
            log_event("warning", "traffic", "Клиент отключён из-за превышения лимита трафика", {"client_id": client.id, "name": client.name})
        except Exception as exc:
            await db.rollback()
            await ClientService.restore_committed_configs(db)
            raise HTTPException(status_code=502, detail=f"Не удалось отключить клиента после превышения лимита: {exc}") from exc
    await db.commit()
    return {"id": client.id, "traffic_used": client.traffic_total, "is_active": client.is_active}


@router.post("/{client_id}/reset-traffic")
async def reset_client_traffic(client_id: int, db: AsyncSession = Depends(get_db)):
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    client.traffic_total = client.traffic_used = 0
    refresh_period(client)
    client.monthly_traffic_up = client.monthly_traffic_down = 0
    result = await db.execute(select(ClientProfile).where(ClientProfile.client_id == client.id))
    for profile in result.scalars().all():
        profile.traffic_up = profile.traffic_down = 0
    try:
        await _sync_protocols(db)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await ClientService.restore_committed_configs(db)
        raise HTTPException(status_code=502, detail=f"Не удалось сбросить трафик и применить доступ: {exc}") from exc
    return {"id": client.id, "traffic_used": 0}
