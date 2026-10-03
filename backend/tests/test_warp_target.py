"""Проверка страны и отката маршрута WARP ноды до сохранения настроек."""
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.setting import Setting
from app.services.warp import WarpService
from app.services.xray import XrayService
from conftest import TestingSessionLocal


@pytest.mark.asyncio
@pytest.mark.parametrize("trace,expected", [
    ({"ip": "203.0.113.1", "country": "RU", "warp": "on"}, 422),
    ({"ip": "203.0.113.1", "country": "DE", "warp": "off"}, 422),
    ({"ip": "203.0.113.1", "country": "DE", "warp": "on"}, 200),
])
async def test_country_and_warp_checked_before_route_saved(auth_headers, trace, expected):
    with patch.object(WarpService, "test_target", return_value=trace), \
         patch("app.api.warp.ClientService.sync_xray_clients", return_value=(True, "ok")) as sync:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put("/api/v1/warp/target", headers=auth_headers,
                json={"node_id": 1, "expected_country": "DE"})
        assert response.status_code == expected
        assert sync.await_count == (1 if expected == 200 else 0)
    async with TestingSessionLocal() as db:
        saved = await db.get(Setting, "warp.node_id")
        assert (saved.value if saved else None) == ("1" if expected == 200 else None)


@pytest.mark.asyncio
async def test_xray_failure_preserves_previous_target(auth_headers):
    async with TestingSessionLocal() as db:
        db.add(Setting(key="warp.node_id", value="")); await db.commit()
    with patch.object(WarpService, "test_target", return_value={"ip":"203.0.113.1","country":"DE","warp":"on"}), \
         patch("app.api.warp.ClientService.sync_xray_clients", return_value=(False, "Ошибка Xray")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put("/api/v1/warp/target", headers=auth_headers, json={"node_id":1})
        assert response.status_code == 502
    async with TestingSessionLocal() as db:
        assert (await db.get(Setting,"warp.node_id")).value == ""


@pytest.mark.asyncio
async def test_remote_target_never_disconnects_local_warp(auth_headers):
    async with TestingSessionLocal() as db:
        db.add(Setting(key="warp.node_id", value="1")); await db.commit()
    with patch.object(WarpService,"disconnect") as disconnect:
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
            response=await client.post("/api/v1/warp/disconnect",headers=auth_headers)
        assert response.status_code == 409
        disconnect.assert_not_awaited()


def test_remote_warp_does_not_change_default_exit():
    node=SimpleNamespace(id=1,host="203.0.113.2",port=443,secret="test",protocol="trojan",is_enabled=True)
    config=json.loads(XrayService.generate_config([],active_node=node,profile_options={"warp_usage":"rules","warp_node_id":1}))
    warp=next(o for o in config["outbounds"] if o["tag"]=="warp")
    assert warp["streamSettings"]["sockopt"]["dialerProxy"]=="node-1"
    assert config["outbounds"][0]["tag"]=="node-1"
    assert warp["settings"]["servers"][0]["address"]=="127.0.0.1"


def test_missing_warp_node_never_falls_back_to_local_proxy():
    with pytest.raises(ValueError,match="нода WARP"):
        XrayService.generate_config([],profile_options={"warp_usage":"rules","warp_node_id":1})
