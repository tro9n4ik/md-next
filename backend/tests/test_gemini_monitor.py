import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.main import app
from app.models.node import Node
from app.models.setting import Setting
from app.services import gemini_monitor as module
from app.services.gemini_monitor import GeminiMonitor, classify_page, monitor_status
from conftest import TestingSessionLocal


@pytest.mark.parametrize("url,code,html,status", [
    ("https://gemini.google.com/unsupportedcountry", 200, "", "region_blocked"),
    ("https://gemini.google.com/app", 200, "<h1>Gemini isn’t currently supported in your country.</h1>", "region_blocked"),
    ("https://gemini.google.com/app", 403, "<p>Gemini is not available in your region.</p>", "region_blocked"),
    ("https://gemini.google.com/app", 429, "Gemini isn't available in your country", "unknown"),
    ("https://gemini.google.com/app", 503, "", "unknown"),
    ("https://accounts.google.com/signin", 200, "Gemini isn't available in your country", "unknown"),
    ("https://gemini.google.com/app", 200, "<h1>Gemini</h1><script>Gemini isn't available in your country</script>", "page_loaded"),
    ("https://gemini.google.com/app", 200, "<script>Gemini isn't available in your country</script>", "unknown"),
    ("https://gemini.google.com/app", 200, "<h1>Gemini</h1><template>Gemini isn't available in your country</template>", "page_loaded"),
    ("https://gemini.google.com/", 200, "Welcome to Gemini", "unknown"),
    ("https://gemini.google.com/app", 200, "Verify you're human", "unknown"),
    ("https://gemini.google.com/app", 200, "<p>If Gemini isn't available in your country, see Help.</p>", "page_loaded"),
    ("https://gemini.google.com/app", 200, "<p>Gemini isn’t <b>currently</b> supported in your country.</p>", "region_blocked"),
])
def test_only_explicit_regional_response_is_blocked(url, code, html, status):
    assert classify_page(url, code, html)["status"] == status


async def prepare(db, *, enabled=True):
    node = Node(id=1, name="Нода", host="203.0.113.5", port=443, protocol="trojan",
                secret="not-a-real-node-secret", is_enabled=True)
    db.add_all([node, Setting(key="active_node_id", value="1"),
                Setting(key="warp.usage", value="rules"),
                Setting(key=module.ENABLED_KEY, value=str(enabled).lower())])
    await db.commit()
    return node


async def seed(db, **values):
    await module._save_state(db, 1, values)


BLOCKED = {"status": "region_blocked", "message": "Регион не поддерживается.",
           "ip": "104.28.1.2", "country": "DE", "warp": "on"}
LOADED = {**BLOCKED, "status": "page_loaded", "message": "Страница открылась."}


@pytest.mark.asyncio
async def test_defaults_are_off_and_do_not_probe():
    async with TestingSessionLocal() as db:
        await prepare(db, enabled=False)
        monitor = GeminiMonitor()
        with patch.object(module, "test_node_proxy", AsyncMock()) as probe:
            assert (await monitor_status(db))["enabled"] is False
            assert (await monitor.check(db, automatic=True))["status"] == "disabled"
            probe.assert_not_called()


@pytest.mark.asyncio
async def test_two_confirmations_rotate_and_verify_same_node_and_port():
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, status="region_blocked", blocked_checks=1)
        with patch.object(module, "test_node_proxy", AsyncMock(side_effect=[BLOCKED, LOADED])) as probe, \
             patch.object(module.WarpService, "control_node", AsyncMock(return_value={"changed": True})) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        assert result["status"] == "page_loaded"
        assert result["blocked_checks"] == 0
        assert len(result["rotation_attempts"]) == 1
        assert rotate.call_args.args[1:] == ("rotate", True)
        assert [call.args[0].id for call in probe.call_args_list] == [1, 1]
        assert [call.args[1] for call in probe.call_args_list] == [40000, 40000]
        assert (await module._load_state(db, 1))["last_rotation_changed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [BLOCKED, LOADED, {"status": "unknown", "message": "CAPTCHA"}])
async def test_manual_check_never_rotates(result):
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, blocked_checks=9)
        with patch.object(module, "test_node_proxy", AsyncMock(return_value=result)), \
             patch.object(module.WarpService, "control_node", AsyncMock()) as rotate:
            await GeminiMonitor().check(db)
        rotate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_result", [LOADED, OSError("network"), {"status": "unknown", "message": "HTTP 429"}])
async def test_no_rotation_on_network_errors_or_ambiguous_response(probe_result):
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, blocked_checks=9)
        probe = AsyncMock(side_effect=probe_result) if isinstance(probe_result, Exception) else AsyncMock(return_value=probe_result)
        with patch.object(module, "test_node_proxy", probe), \
             patch.object(module.WarpService, "control_node", AsyncMock()) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        assert result["blocked_checks"] == 0
        rotate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    {"blocked_checks": 0},
    {"blocked_checks": 1, "rotation_attempts": [9999]},
    {"blocked_checks": 1, "rotation_attempts": [9000, 9100, 9200]},
])
async def test_confirmation_cooldown_and_persistent_budget_prevent_rotation(state):
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, **state)
    # Новый экземпляр и сессия имитируют перезапуск backend: лимит остаётся в БД.
    async with TestingSessionLocal() as db:
        with patch.object(module.time, "time", return_value=10000), \
             patch.object(module, "test_node_proxy", AsyncMock(return_value=BLOCKED)), \
             patch.object(module.WarpService, "control_node", AsyncMock()) as rotate:
            await GeminiMonitor().check(db, automatic=True)
        rotate.assert_not_called()


@pytest.mark.asyncio
async def test_interval_skips_repeated_probe():
    async with TestingSessionLocal() as db:
        await prepare(db)
        with patch.object(module, "test_node_proxy", AsyncMock(return_value=BLOCKED)) as probe:
            monitor = GeminiMonitor()
            await monitor.check(db, automatic=True)
            await monitor.check(db, automatic=True)
        assert probe.await_count == 1


@pytest.mark.asyncio
async def test_expired_attempts_are_removed_and_native_port_is_used():
    async with TestingSessionLocal() as db:
        await prepare(db)
        db.add(Setting(key="warp.ipv4_only", value="false"))
        await db.commit()
        await seed(db, blocked_checks=1, rotation_attempts=[1, 2, 3])
        with patch.object(module.time, "time", return_value=100000), \
             patch.object(module, "test_node_proxy", AsyncMock(side_effect=[BLOCKED, LOADED])) as probe, \
             patch.object(module.WarpService, "control_node", AsyncMock(return_value={"changed": False})) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        assert result["rotation_attempts"] == [100000]
        assert "прежний IP" in result["rotation_note"]
        assert rotate.call_args.args[1:] == ("rotate", False)
        assert [call.args[1] for call in probe.call_args_list] == [40001, 40001]


@pytest.mark.asyncio
async def test_second_node_does_not_inherit_first_node_budget_or_confirmations():
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, blocked_checks=9, rotation_attempts=[9998, 9999, 10000])
        db.add(Node(id=2, name="Другая нода", host="203.0.113.6", port=443, protocol="trojan",
                    secret="another-not-real-secret", is_enabled=True))
        (await db.get(Setting, "active_node_id")).value = "2"
        await db.commit()
        with patch.object(module, "test_node_proxy", AsyncMock(return_value=BLOCKED)), \
             patch.object(module.WarpService, "control_node", AsyncMock()) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        assert result["rotation_attempts"] == []
        assert result["blocked_checks"] == 1
        rotate.assert_not_called()


@pytest.mark.asyncio
async def test_rotation_failure_is_counted_before_external_effect():
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, blocked_checks=1)
        async def fail(*args):
            async with TestingSessionLocal() as other:
                assert len((await module._load_state(other, 1))["rotation_attempts"]) == 1
            raise RuntimeError("handler unavailable")
        with patch.object(module, "test_node_proxy", AsyncMock(return_value=BLOCKED)), \
             patch.object(module.WarpService, "control_node", AsyncMock(side_effect=fail)):
            result = await GeminiMonitor().check(db, automatic=True)
        assert result["status"] == "unknown"
        assert len(result["rotation_attempts"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["route", "disabled", "warp_off"])
async def test_change_during_probe_prevents_rotation(change):
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, blocked_checks=1)
        async def probe(*args):
            async with TestingSessionLocal() as other:
                key, value = {"route": ("active_node_id", "direct:manual"),
                              "disabled": (module.ENABLED_KEY, "false"),
                              "warp_off": ("warp.usage", "off")}[change]
                row = await other.get(Setting, key)
                row.value = value
                await other.commit()
            return BLOCKED
        with patch.object(module, "test_node_proxy", AsyncMock(side_effect=probe)), \
             patch.object(module.WarpService, "control_node", AsyncMock()) as rotate:
            await GeminiMonitor().check(db, automatic=True)
        rotate.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_warp_does_not_probe():
    async with TestingSessionLocal() as db:
        await prepare(db)
        (await db.get(Setting, "warp.usage")).value = "off"
        await db.commit()
        with patch.object(module, "test_node_proxy", AsyncMock()) as probe:
            assert (await GeminiMonitor().check(db, automatic=True))["status"] == "skipped"
        probe.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_routes_require_auth_and_persist_toggle(auth_headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, path in [("get", "monitor"), ("put", "monitor"), ("post", "check")]:
            assert (await client.request(method, "/api/v1/warp/gemini/" + path, json={"enabled": True})).status_code == 401
        response = await client.get("/api/v1/warp/gemini/monitor", headers=auth_headers)
        assert response.json()["enabled"] is False
        response = await client.put("/api/v1/warp/gemini/monitor", headers=auth_headers, json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert (await client.put("/api/v1/warp/gemini/monitor", headers=auth_headers, json={"enabled": "yes"})).status_code == 422


@pytest.mark.asyncio
async def test_probe_uses_socks_and_refuses_non_gemini_redirect():
    calls = []
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["proxy"] == "socks5://127.0.0.1:23456"
        assert kwargs["trust_env"] is False
        kwargs.pop("proxy")
        return original(transport=httpx.MockTransport(response), **kwargs)
    with patch.object(module.WarpService, "test_proxy", AsyncMock(return_value=BLOCKED)), \
         patch.object(module.httpx, "AsyncClient", client):
        result = await module.probe_proxy(23456, timeout=1)
    assert result["status"] == "unknown"
    assert calls == [module.GEMINI_URL]


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed_or_used_to_rotate():
    async with TestingSessionLocal() as db:
        await prepare(db)
        with patch.object(module, "test_node_proxy", AsyncMock(side_effect=asyncio.CancelledError)), \
             patch.object(module.WarpService, "control_node", AsyncMock()) as rotate:
            with pytest.raises(asyncio.CancelledError):
                await GeminiMonitor().check(db, automatic=True)
        rotate.assert_not_called()
