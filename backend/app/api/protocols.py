import re
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.client import Client
from app.models.node import Node
from app.models.setting import Setting
from app.services.awg import AWGService
from app.services.client_service import ClientService
from app.services.links_guard import (
    describe_identity_change,
    find_node_host_conflict,
    node_hosts,
    resolve_identity_fields,
)
from app.services.profiles import PROFILE_KINDS, enabled_profile_kinds, get_profile_settings, create_profiles
from app.services.nginx import apply_xhttp_tls_path

router = APIRouter(prefix="/api/v1/settings/protocols", tags=["Settings"], dependencies=[Depends(get_current_user)])


class ProtocolSettingsRequest(BaseModel):
    enabled: dict[str, bool] = Field(default_factory=dict)
    ports: dict[str, int] = Field(default_factory=dict)
    paths: dict[str, str] = Field(default_factory=dict)
    reality: dict[str, str] = Field(default_factory=dict)
    modes: dict[str, str] = Field(default_factory=dict)
    confirm_link_identity_change: bool = False


def _validate_path(path: str) -> str:
    path = path.strip()
    if not path.startswith("/") or len(path) > 180 or ".." in path or not re.fullmatch(r"/[A-Za-z0-9/_-]*", path):
        raise HTTPException(status_code=422, detail="Путь должен начинаться с / и содержать только латинские буквы, цифры, /, _ или -")
    return path


def _validate_host(value: str, label: str) -> str:
    value = value.strip()
    if len(value) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", value) or value.startswith(".") or value.endswith("."):
        raise HTTPException(status_code=422, detail=f"{label}: укажите домен или IPv4 без протокола и пути")
    return value


def _validate_target(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"([A-Za-z0-9.-]+):(\d{1,5})", value)
    if not match or not 1 <= int(match.group(2)) <= 65535:
        raise HTTPException(status_code=422, detail="Reality target должен иметь вид домен:порт или IPv4:порт")
    return value


@router.get("")
async def get_protocol_settings(db: AsyncSession = Depends(get_db)):
    values = await get_profile_settings(db)
    return {
        "profiles": [
            {"kind": kind, "enabled": values.get(f"profiles.enabled.{kind}", "true" if kind in ("vless_reality_tcp", "awg") else "false").lower() in ("true", "1", "yes")}
            for kind in PROFILE_KINDS
        ],
        "ports": {
            "vless_xhttp_reality": int(values.get("profiles.port.vless_xhttp_reality", "2053")),
            "hysteria2": int(values.get("profiles.port.hysteria2", "443")),
        },
        "paths": {
            "vless_xhttp_reality": values.get("profiles.path.vless_xhttp_reality", "/"),
            "vless_xhttp_tls": values.get("profiles.path.vless_xhttp_tls", "/md-next-xhttp"),
        },
        "reality": {
            "server_address": values["protocol.reality.server_address"],
            "target": values["protocol.reality.target"],
            "server_name": values["protocol.reality.server_name"],
            "fingerprint": values["protocol.reality.fingerprint"],
            "short_id": values["protocol.reality.short_id"],
            "public_key": values["protocol.reality.public_key"],
            "private_key_set": bool(values.get("protocol.reality.private_key", os.getenv("XRAY_PRIVATE_KEY", ""))),
            "flow": values["protocol.reality.flow"],
        },
        "modes": {
            "vless_xhttp_reality": values["protocol.xhttp.reality_mode"],
            "vless_xhttp_tls": values["protocol.xhttp.tls_mode"],
        },
    }


@router.put("")
async def update_protocol_settings(req: ProtocolSettingsRequest, db: AsyncSession = Depends(get_db)):
    if set(req.enabled) - set(PROFILE_KINDS):
        raise HTTPException(status_code=422, detail="Неизвестный тип профиля")
    if any(kind not in ("vless_xhttp_reality", "hysteria2") or not 1 <= port <= 65535 for kind, port in req.ports.items()):
        raise HTTPException(status_code=422, detail="Некорректный порт")
    allowed_paths = {"vless_xhttp_reality", "vless_xhttp_tls"}
    if set(req.paths) - allowed_paths:
        raise HTTPException(status_code=422, detail="Неизвестный тип пути")
    paths = {kind: _validate_path(path) for kind, path in req.paths.items()}
    if paths.get("vless_xhttp_tls") == "/":
        raise HTTPException(status_code=422, detail="Для XHTTP TLS требуется непустой секретный путь")
    allowed_reality = {"server_address", "target", "server_name", "fingerprint", "short_id", "public_key", "private_key", "flow"}
    if set(req.reality) - allowed_reality:
        raise HTTPException(status_code=422, detail="Неизвестный параметр Reality")
    reality = {key: value.strip() for key, value in req.reality.items()}
    known_node_hosts = node_hosts(
        (await db.execute(select(Node).where(Node.is_enabled.is_(True)))).scalars().all()
    )
    conflict = find_node_host_conflict(reality, known_node_hosts)
    if conflict:
        raise HTTPException(
            status_code=422,
            detail=(
                "Публичный адрес и Reality SNI должны указывать на панель, а не на ноду. "
                "Адрес ноды в ссылке приведёт к тому, что ссылка перестанет работать после замены ноды."
            ),
        )
    current_values = await get_profile_settings(db)
    changed_identity = resolve_identity_fields(
        {field: current_values.get(f"protocol.reality.{field}") for field in ("server_address", "server_name", "fingerprint", "short_id", "public_key", "flow")},
        reality,
    )
    if changed_identity and not req.confirm_link_identity_change:
        raise HTTPException(status_code=409, detail=describe_identity_change(changed_identity))
    if "server_address" in reality:
        reality["server_address"] = _validate_host(reality["server_address"], "Публичный адрес")
    if "server_name" in reality:
        reality["server_name"] = _validate_host(reality["server_name"], "Reality SNI")
    if "target" in reality:
        reality["target"] = _validate_target(reality["target"])
    if "fingerprint" in reality and reality["fingerprint"] not in {"chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random", "randomized"}:
        raise HTTPException(status_code=422, detail="Неподдерживаемый fingerprint")
    if "short_id" in reality and (len(reality["short_id"]) > 16 or len(reality["short_id"]) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", reality["short_id"])):
        raise HTTPException(status_code=422, detail="Short ID должен содержать до 16 шестнадцатеричных символов чётной длины")
    if "public_key" in reality and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", reality["public_key"]):
        raise HTTPException(status_code=422, detail="Некорректный Reality public key")
    if "private_key" in reality and reality["private_key"] and not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", reality["private_key"]):
        raise HTTPException(status_code=422, detail="Некорректный Reality private key")
    allowed_modes = {"auto", "packet-up", "stream-up", "stream-one"}
    if set(req.modes) - {"vless_xhttp_reality", "vless_xhttp_tls"} or any(mode not in allowed_modes for mode in req.modes.values()):
        raise HTTPException(status_code=422, detail="Некорректный режим XHTTP")
    if "flow" in reality and reality["flow"] not in {"", "xtls-rprx-vision"}:
        raise HTTPException(status_code=422, detail="Поддерживаются только пустой Flow или xtls-rprx-vision")
    old_enabled = await enabled_profile_kinds(db)
    updates = {f"profiles.enabled.{kind}": str(value).lower() for kind, value in req.enabled.items()}
    updates.update({f"profiles.port.{kind}": str(value) for kind, value in req.ports.items()})
    updates.update({f"profiles.path.{kind}": value for kind, value in paths.items()})
    updates.update({f"protocol.reality.{key}": value for key, value in reality.items() if key != "private_key" or value})
    updates.update({f"protocol.xhttp.{kind.removeprefix('vless_xhttp_')}_mode": value for kind, value in req.modes.items()})
    if "flow" in reality:
        updates["protocol.reality.flow"] = reality["flow"]
    nginx_path_applied = False
    xray_applied = False
    try:
        for key, value in updates.items():
            setting = await db.get(Setting, key)
            if setting:
                setting.value = value
            else:
                db.add(Setting(key=key, value=value))
        if (await enabled_profile_kinds(db)) - old_enabled:
            for client in (await db.execute(select(Client))).scalars().all():
                await create_profiles(db, client)
        current_enabled = await enabled_profile_kinds(db)
        if "vless_xhttp_tls" in current_enabled:
            values = await get_profile_settings(db)
            await apply_xhttp_tls_path(values["profiles.path.vless_xhttp_tls"])
            nginx_path_applied = True
        ok, reason = await ClientService.sync_xray_clients(db)
        if not ok:
            raise RuntimeError(reason)
        xray_applied = True
        ok, reason = await AWGService.sync_server_config(db)
        if not ok:
            raise RuntimeError(reason)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if nginx_path_applied:
            try:
                await apply_xhttp_tls_path(current_values["profiles.path.vless_xhttp_tls"])
            except Exception:
                from app.services.events import log_event
                log_event("error", "nginx", "Не удалось восстановить прежний путь XHTTP после ошибки сохранения")
        if xray_applied:
            await ClientService.restore_committed_configs(db)
        detail = str(exc)
        private_key = reality.get("private_key", "")
        if private_key:
            detail = detail.replace(private_key, "[скрыто]")
        raise HTTPException(status_code=502, detail=f"Не удалось применить настройки протоколов: {detail}") from exc
    return await get_protocol_settings(db)
