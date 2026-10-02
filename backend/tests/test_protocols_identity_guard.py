import unittest.mock as mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.future import select

from app.db.database import AsyncSessionLocal
from app.main import app
from app.models.node import Node
from app.models.setting import Setting

REALITY = {
    "server_address": "panel.example.com",
    "server_name": "panel.example.com",
    "target": "127.0.0.1:8080",
    "fingerprint": "chrome",
    "short_id": "a1b2c3d4",
    "public_key": "StablePublicKeyValue",
    "flow": "xtls-rprx-vision",
}


async def _request(method, path, auth_headers, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=auth_headers, **kwargs)


async def _seed_identity():
    async with AsyncSessionLocal() as session:
        for key, value in REALITY.items():
            session.add(Setting(key=f"protocol.reality.{key}", value=value))
        await session.commit()


@pytest.mark.asyncio
async def test_saving_unchanged_reality_block_is_applied(auth_headers):
    await _seed_identity()
    with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
         mock.patch("app.api.protocols.AWGService.sync_server_config", return_value=(True, "ok")):
        response = await _request("PUT", "/api/v1/settings/protocols", auth_headers, json={"reality": dict(REALITY)})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_identity_change_requires_confirmation(auth_headers):
    await _seed_identity()
    with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
         mock.patch("app.api.protocols.AWGService.sync_server_config", return_value=(True, "ok")):
        blocked = await _request(
            "PUT", "/api/v1/settings/protocols", auth_headers,
            json={"reality": {**REALITY, "short_id": "ffffffff"}},
        )
        assert blocked.status_code == 409
        assert "short ID" in blocked.json()["detail"]

        allowed = await _request(
            "PUT", "/api/v1/settings/protocols", auth_headers,
            json={"reality": {**REALITY, "short_id": "ffffffff"}, "confirm_link_identity_change": True},
        )
        assert allowed.status_code == 200
    async with AsyncSessionLocal() as session:
        saved = await session.get(Setting, "protocol.reality.short_id")
        assert saved.value == "ffffffff"


@pytest.mark.asyncio
async def test_target_only_change_is_not_blocked(auth_headers):
    await _seed_identity()
    with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
         mock.patch("app.api.protocols.AWGService.sync_server_config", return_value=(True, "ok")):
        response = await _request(
            "PUT", "/api/v1/settings/protocols", auth_headers,
            json={"reality": {**REALITY, "target": "127.0.0.1:9090"}},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_node_ip_in_public_address_is_rejected(auth_headers):
    async with AsyncSessionLocal() as session:
        session.add(Node(name="node", host="203.0.113.77", port=443, protocol="trojan", secret="s", is_enabled=True))
        await session.commit()

    response = await _request(
        "PUT", "/api/v1/settings/protocols", auth_headers,
        json={"reality": {**REALITY, "server_address": "203.0.113.77"}},
    )
    assert response.status_code == 422
    assert "ноду" in response.json()["detail"]


@pytest.mark.asyncio
async def test_disabled_node_ip_is_not_treated_as_conflict(auth_headers):
    async with AsyncSessionLocal() as session:
        node = Node(name="old", host="203.0.113.88", port=443, protocol="trojan", secret="s", is_enabled=False)
        session.add(node)
        await session.commit()

    with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
         mock.patch("app.api.protocols.AWGService.sync_server_config", return_value=(True, "ok")):
        response = await _request(
            "PUT", "/api/v1/settings/protocols", auth_headers,
            json={"reality": {**REALITY, "server_address": "203.0.113.88"}, "confirm_link_identity_change": True},
        )
    assert response.status_code == 200