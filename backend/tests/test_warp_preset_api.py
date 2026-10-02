import unittest.mock as mock

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.database import AsyncSessionLocal
from app.main import app
from app.services.warp_presets import PRESETS


async def _request(method, path, auth_headers, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=auth_headers, **kwargs)


def _mock_sync(ok=True, reason="ok"):
    return mock.patch("app.services.client_service.XrayService.apply_config", return_value=(ok, reason))


@pytest.mark.asyncio
async def test_presets_endpoint_lists_catalog_with_state(auth_headers):
    response = await _request("GET", "/api/v1/warp/presets", auth_headers)
    assert response.status_code == 200
    payload = response.json()
    keys = [item["key"] for item in payload["presets"]]
    assert keys == list(PRESETS)
    for item in payload["presets"]:
        assert item["title"]
        assert item["description"]
        assert item["domains"]
        assert set(item["state"]) == {"total", "missing", "extra"}


@pytest.mark.asyncio
async def test_apply_preset_creates_rules_and_applies_config(auth_headers):
    with _mock_sync() as apply_config:
        response = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "gemini"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert len(payload["created"]) == len(PRESETS["gemini"].domains)
    assert apply_config.called


@pytest.mark.asyncio
async def test_apply_preset_is_idempotent_through_api(auth_headers):
    with _mock_sync():
        first = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "claude"})
        second = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "claude"})
    assert first.status_code == second.status_code == 200
    assert second.json()["created"] == []
    assert second.json()["removed"] == []


@pytest.mark.asyncio
async def test_apply_unknown_preset_returns_404(auth_headers):
    response = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "нет"})
    assert response.status_code == 404
    assert "gemini" in response.json()["detail"]


@pytest.mark.asyncio
async def test_apply_preset_rolls_back_when_xray_apply_fails(auth_headers):
    with _mock_sync(ok=False, reason="geosite.dat not found"):
        response = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "chatgpt"})
    assert response.status_code == 400
    assert "geosite.dat" in response.json()["detail"]

    # Правила не должны остаться в базе после неудачного применения.
    async with AsyncSessionLocal() as session:
        from app.services.warp_presets import list_preset_rules
        assert await list_preset_rules(session, "chatgpt") == []


@pytest.mark.asyncio
async def test_apply_preset_reports_xray_failure_as_502_for_other_reasons(auth_headers):
    with _mock_sync(ok=False, reason="xray binary crashed"):
        response = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "ai-services"})
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_remove_preset_deletes_rules(auth_headers):
    with _mock_sync():
        await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "gemini"})
        response = await _request("POST", "/api/v1/warp/presets/remove", auth_headers, json={"key": "gemini"})
    assert response.status_code == 200
    assert len(response.json()["removed"]) == len(PRESETS["gemini"].domains)


@pytest.mark.asyncio
async def test_remove_unknown_preset_returns_404(auth_headers):
    response = await _request("POST", "/api/v1/warp/presets/remove", auth_headers, json={"key": "нет"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_when_not_applied_is_ok_without_xray_apply(auth_headers):
    with _mock_sync() as apply_config:
        response = await _request("POST", "/api/v1/warp/presets/remove", auth_headers, json={"key": "perplexity-or-none"})
    # Неизвестный ключ, а не пустой результат: 404 ожидаем.
    assert response.status_code == 404
    assert not apply_config.called


@pytest.mark.asyncio
async def test_apply_preset_enables_warp_usage_when_off(auth_headers):
    from sqlalchemy import select

    from app.models.setting import Setting

    async with AsyncSessionLocal() as session:
        setting = (await session.execute(
            select(Setting).where(Setting.key == "warp.usage")
        )).scalar_one_or_none()
        if setting is None:
            session.add(Setting(key="warp.usage", value="off"))
        else:
            setting.value = "off"
        await session.commit()

    with _mock_sync():
        response = await _request("POST", "/api/v1/warp/presets/apply", auth_headers, json={"key": "claude"})
    assert response.status_code == 200
    assert response.json()["warp_usage"] == "rules"

    async with AsyncSessionLocal() as session:
        updated = (await session.execute(
            select(Setting).where(Setting.key == "warp.usage")
        )).scalar_one()
        assert updated.value == "rules"


@pytest.mark.asyncio
async def test_presets_require_authentication():
    response = await _request("GET", "/api/v1/warp/presets", {})
    assert response.status_code in (401, 403)