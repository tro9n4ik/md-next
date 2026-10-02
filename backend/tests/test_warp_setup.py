"""Бесплатное включение WARP без сброса регистрации и с откатом при ошибке."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models.setting import Setting
from app.services.warp import WarpService
from conftest import TestingSessionLocal


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True])
async def test_free_setup_registers_only_when_missing_and_never_uses_license(registered):
    previous = {"installed": True, "service_active": True, "registered": registered,
                "state": "Disconnected", "mode": "warp", "port": 40000}
    async with TestingSessionLocal() as db:
        with patch.object(WarpService, "status", return_value=previous), \
             patch.object(WarpService, "set_mode", return_value=(True, "ok")) as mode, \
             patch.object(WarpService, "_run_process", return_value=(1, "", "Missing registration")), \
             patch.object(WarpService, "register", return_value=(True, "ok")) as register, \
             patch.object(WarpService, "connect", return_value=(True, "ok")), \
             patch.object(WarpService, "test_proxy", return_value={"ip": "203.0.113.1", "country": "NL", "warp": "on"}), \
             patch.object(WarpService, "set_license", side_effect=AssertionError("Лицензия не нужна")):
            success, message = await WarpService.setup_warp_proxy(db)
        assert success and "без лицензии" in message
        assert register.await_count == (0 if registered else 1)
        mode.assert_awaited_once_with(db, "proxy", 40000, persist=False)
        values = dict((await db.execute(select(Setting.key, Setting.value))).all())
        assert values["warp.mode"] == "proxy"
        assert values["warp.proxy_port"] == "40000"
        assert "warp.usage" not in values


@pytest.mark.asyncio
async def test_failed_proxy_check_restores_runtime_and_preserves_database():
    previous = {"installed": True, "service_active": True, "registered": True,
                "state": "Connected", "mode": "proxy", "port": 40000, "runtime_port": 41000}
    async with TestingSessionLocal() as db:
        db.add(Setting(key="warp.proxy_port", value="40000"))
        await db.commit()
        with patch.object(WarpService, "status", return_value=previous), \
             patch.object(WarpService, "set_mode", return_value=(True, "ok")) as mode, \
             patch.object(WarpService, "connect", return_value=(True, "ok")) as connect, \
             patch.object(WarpService, "test_proxy", side_effect=httpx.ConnectError("Нет соединения")), \
             patch("app.services.warp.asyncio.sleep", new=AsyncMock()), \
             patch.object(WarpService, "register") as register:
            success, _ = await WarpService.setup_warp_proxy(db)
        assert not success
        mode.assert_awaited_with(db, "proxy", 41000, persist=False)
        assert connect.await_count == 2
        register.assert_not_awaited()
        values = dict((await db.execute(select(Setting.key, Setting.value))).all())
        assert values == {"warp.proxy_port": "40000"}


@pytest.mark.asyncio
async def test_registration_service_error_does_not_recreate_account():
    previous = {"installed": True, "service_active": True, "registered": False,
                "state": "Disconnected", "mode": "proxy", "port": 40000}
    async with TestingSessionLocal() as db:
        with patch.object(WarpService, "status", return_value=previous), \
             patch.object(WarpService, "set_mode", return_value=(True, "ok")), \
             patch.object(WarpService, "_run_process", return_value=(1, "", "IPC error")), \
             patch.object(WarpService, "disconnect", return_value=(True, "ok")), \
             patch.object(WarpService, "register") as register:
            success, _ = await WarpService.setup_warp_proxy(db)
        assert not success
        register.assert_not_awaited()


def test_settings_output_recognizes_warp_proxy():
    parsed = WarpService.parse_status("Status update: Connected\n(user set) Mode: WarpProxy\n(user set) Proxy port: 41000", "Account type: Free")
    assert parsed["mode"] == "proxy"
    assert parsed["port"] == 41000
    assert parsed["registered"]


@pytest.mark.asyncio
async def test_registration_waits_for_cloudflare_and_explains_api_error():
    with patch.object(WarpService, "_run_process", return_value=(1, "", "Failed to communicate with the WARP API")) as process:
        success, message = await WarpService.register()
    assert not success
    assert "Лицензионный ключ не требуется" in message
    assert process.await_args.kwargs["timeout"] == WarpService.REGISTRATION_TIMEOUT
