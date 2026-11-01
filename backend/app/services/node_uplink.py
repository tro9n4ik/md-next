"""Управление промежуточным WARP без изменения клиентских подключений."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import time

from sqlalchemy import select

from app.models.setting import Setting
from app.services.node_transport import node_uplink_warp_port, resolve_uplink_port

ENABLED_KEY = "warp.uplink.enabled"
PORT_KEY = "warp.uplink.port"
CHECK_KEY = "warp.uplink.last_check"
PREPARING = {"queued", "installing", "registering", "connecting"}


async def settings(db) -> dict:
    rows = await db.execute(select(Setting.key, Setting.value).where(Setting.key.in_([ENABLED_KEY, PORT_KEY])))
    values = dict(rows.all())
    explicit = values.get(ENABLED_KEY)
    if explicit is not None and explicit not in {"true", "false"}:
        raise ValueError("Некорректное состояние канала WARP")
    try:
        legacy_port = node_uplink_warp_port()
    except ValueError:
        if explicit is None:
            raise
        legacy_port = None
    port = int(values[PORT_KEY]) if PORT_KEY in values else legacy_port or 40000
    resolve_uplink_port(port)
    return {"enabled": explicit == "true" if explicit is not None else legacy_port is not None,
            "port": port, "source": "panel" if explicit is not None else "environment" if legacy_port else "default"}


async def get_uplink_port(db) -> int | None:
    value = await settings(db)
    return value["port"] if value["enabled"] else None


async def runtime_request(action: str, **fields) -> dict:
    from app.services.privileges import enabled, call
    if enabled():
        code, output, _ = await call("warp-uplink-" + action, **fields)
        if code:
            raise RuntimeError("Не удалось выполнить подготовку WARP через системный помощник")
        return json.loads(output)
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Для подготовки WARP требуется системный помощник панели")
    path = Path(__file__).resolve().parents[3] / "scripts" / "warp-uplink-runtime.py"
    spec = importlib.util.spec_from_file_location("warp_uplink_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if action == "status":
        return await asyncio.to_thread(module.status)
    if action == "prepare":
        return await asyncio.to_thread(module.start, fields["node_id"], fields["port"])
    raise ValueError("Неизвестная операция подготовки WARP")


async def local_listener(port: int) -> bool:
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 1)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        return await asyncio.wait_for(reader.readexactly(2), 1) == b"\x05\x00"
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        return False
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def status(db) -> dict:
    from app.services.xray import XrayService
    value = await settings(db)
    node = await XrayService.get_active_node(db)
    ready = await local_listener(value["port"])
    try:
        job = await runtime_request("status")
    except (OSError, RuntimeError, ValueError):
        job = {"phase": "unavailable", "message": "Состояние подготовки недоступно"}
    row = await db.get(Setting, CHECK_KEY, populate_existing=True)
    try:
        check = json.loads(row.value) if row else None
        if not isinstance(check, dict) or check.get("port") != value["port"] or check.get("node_id") != (node.id if node else None):
            check = None
    except (ValueError, TypeError):
        check = None
    preparing = job.get("phase") in PREPARING
    return {**value, "ready": ready, "preparing": preparing, "job": job, "check": check,
            "node_id": node.id if node else None, "node_name": node.name if node else "Нода не выбрана",
            "paused": value["enabled"] and node is None, "can_prepare": bool(node and node.secret) and not preparing}


async def save_value(db, key: str, value: str):
    row = await db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    await db.flush()


async def verify(db, port: int) -> dict:
    from app.services.warp import WarpService
    from app.services.warp_node import test_node_proxy
    from app.services.xray import XrayService
    node = await XrayService.get_active_node(db)
    if not node or not node.secret:
        raise ValueError("Сначала выберите активную ноду в разделе «Узлы»")
    try:
        trace = await WarpService.test_proxy(port, timeout=5)
        if trace.get("warp") not in {"on", "plus"}:
            raise ValueError("Локальный прокси работает без WARP")
    except Exception as exc:
        raise ValueError("Локальный WARP не прошёл проверку. Подготовьте подключение на основном сервере.") from exc
    try:
        async def probe(local_port, *, timeout):
            return await WarpService.test_proxy(local_port, timeout=8)
        await asyncio.wait_for(test_node_proxy(node, 0, probe, control=True, uplink_port=port), 15)
    except Exception as exc:
        raise ValueError("WARP доступен, но канал к выбранной ноде не прошёл проверку. Рабочий маршрут сохранён.") from exc
    return {"ok": True, "message": "Локальный WARP и выход через выбранную ноду проверены",
            "checked_at": time.time(), "port": port, "node_id": node.id}


async def set_enabled(db, enabled: bool) -> dict:
    from app.services.client_service import ClientService
    from app.services.cluster import _route_lock
    from app.services.warp import _setup_lock
    async with _setup_lock, _route_lock:
        previous = await settings(db)
        if enabled:
            try:
                job = await runtime_request("status")
            except (OSError, RuntimeError, ValueError):
                job = {}
            if job.get("phase") in PREPARING:
                raise ValueError("Дождитесь завершения подготовки локального WARP")
        check = await verify(db, previous["port"]) if enabled else None
        try:
            await save_value(db, ENABLED_KEY, str(enabled).lower())
            await save_value(db, PORT_KEY, str(previous["port"]))
            if check:
                await save_value(db, CHECK_KEY, json.dumps(check))
            ok, reason = await ClientService.sync_xray_clients(db)
            if not ok:
                raise RuntimeError("Не удалось применить канал WARP: " + reason)
            await db.commit()
        except Exception:
            await db.rollback()
            await ClientService.restore_committed_configs(db)
            raise
    return await status(db)
