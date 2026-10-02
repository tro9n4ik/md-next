import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, func

from app.models.event import Event
from app.services import events
from conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_log_event_persists_safe_event():
    events.log_event("info", "test", "Тестовое событие", {"client_id": 12, "api_key": "hidden", "nested": {"password": "hidden"}})
    await events.flush_pending_events()
    async with TestingSessionLocal() as db:
        item = (await db.execute(select(Event).where(Event.category == "test"))).scalars().first()
        assert item is not None
        assert item.meta == {"client_id": 12, "nested": {}}


@pytest.mark.asyncio
async def test_cleanup_removes_old_and_keeps_latest_5000():
    now = dt.datetime.now(dt.timezone.utc)
    async with TestingSessionLocal() as db:
        db.add(Event(level="info", category="old", message="Старое событие", ts=now - dt.timedelta(days=31)))
        db.add_all(Event(level="info", category="bulk", message="Событие", ts=now - dt.timedelta(minutes=index % 10)) for index in range(5002))
        await db.commit()
        removed_old, removed_excess = await events.cleanup_events(db)
        assert removed_old == 1
        assert removed_excess == 2
        count = await db.scalar(select(func.count()).select_from(Event))
        assert count == 5000


@pytest.mark.asyncio
async def test_log_event_swallows_database_errors():
    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")
        async def __aexit__(self, *args):
            return None

    with patch.object(events.database, "AsyncSessionLocal", BrokenSession):
        events.log_event("error", "test", "Ошибка для теста")
        await events.flush_pending_events()


@pytest.mark.asyncio
async def test_events_endpoint_filters_by_level_and_category():
    from app.api.events import get_events

    async with TestingSessionLocal() as db:
        db.add_all([
            Event(level="info", category="auth", message="Вход"),
            Event(level="error", category="xray", message="Сбой"),
        ])
        await db.commit()
        result = await get_events(limit=50, level="error", category="xray", db=db)
        assert len(result) == 1
        assert result[0].message == "Сбой"


@pytest.mark.asyncio
async def test_health_endpoint_returns_live_named_checks(monkeypatch, tmp_path):
    from app.api.system import get_system_health

    monkeypatch.setenv("AWG_CONFIG_PATH", str(tmp_path / "missing-awg.conf"))
    monkeypatch.setenv("TLS_CERT_PATH", str(tmp_path / "missing-cert.pem"))
    monkeypatch.setattr("app.api.system._service_active", AsyncMock(side_effect=[(True, "active"), (False, "inactive")]))
    monkeypatch.setattr("app.api.system._port_listening", AsyncMock(return_value=True))
    monkeypatch.setattr("app.api.system.bot_manager.status", "running")
    db = AsyncMock()
    db.get.return_value = type("Setting", (), {"value": "off"})()
    result = await get_system_health(db)
    checks = {check["key"]: check for check in result["checks"]}
    assert {"api", "database", "xray", "awg", "nginx", "telegram", "warp", "certificate"} <= checks.keys()
    assert checks["xray"]["status"] == "ok"
    assert checks["nginx"]["status"] == "error"
    assert checks["warp"]["status"] == "disabled"
    assert "degraded" not in str(result).lower()
