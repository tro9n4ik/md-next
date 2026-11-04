import asyncio
import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import nodes
from app.main import app
from app.models.node_invite import NodeInvite
from conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_join_and_register_share_limit_despite_spoofed_headers(monkeypatch):
    monkeypatch.setattr(nodes, "NODE_REGISTRATION_IP_LIMIT", 2)
    transport = ASGITransport(app=app, client=("192.0.2.1", 5000))
    async with AsyncClient(transport=transport, base_url="https://test.example") as api:
        for i in range(2):
            response = await api.get(
                "/api/v1/nodes/join", params={"token": "invalid"},
                headers={"X-Real-IP": f"192.0.2.{10 + i}", "X-Forwarded-For": f"198.51.100.{i}"},
            )
            assert response.status_code == 401
        response = await api.post(
            "/api/v1/nodes/register",
            json={"token": "invalid", "host": "192.0.2.2", "port": 443},
            headers={"X-Real-IP": "192.0.2.33", "X-Forwarded-For": "198.51.100.33"},
        )
        assert response.status_code == 429


@pytest.mark.asyncio
async def test_join_gives_same_response_for_expired_and_unknown_token():
    async with TestingSessionLocal() as db:
        db.add(NodeInvite(
            name="expired", token_hash=nodes._hash_token("expired"),
            expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        ))
        await db.commit()
    transport = ASGITransport(app=app, client=("192.0.2.2", 5000))
    async with AsyncClient(transport=transport, base_url="https://test.example") as api:
        responses = [
            await api.get("/api/v1/nodes/join", params={"token": token})
            for token in ("expired", "invalid")
        ]
    assert [response.status_code for response in responses] == [401, 401]
    assert responses[0].json() == responses[1].json()
    assert min(response.elapsed.total_seconds() for response in responses) >= nodes.NODE_REGISTRATION_MIN_FAILURE_TIME


@pytest.mark.asyncio
async def test_local_proxy_uses_real_ip_and_ignores_forwarded_for(monkeypatch):
    monkeypatch.setattr(nodes, "NODE_REGISTRATION_IP_LIMIT", 1)
    transport = ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with AsyncClient(transport=transport, base_url="https://test.example") as api:
        for real, forwarded, expected in (
            ("192.0.2.10", "198.51.100.10", 401),
            ("192.0.2.10", "198.51.100.11", 429),
            ("192.0.2.11", "198.51.100.10", 401),
        ):
            response = await api.get(
                "/api/v1/nodes/join", params={"token": "invalid"},
                headers={"X-Real-IP": real, "X-Forwarded-For": forwarded},
            )
            assert response.status_code == expected


@pytest.mark.asyncio
async def test_active_invite_cap(monkeypatch, auth_headers):
    monkeypatch.setattr(nodes, "NODE_PENDING_INVITE_LIMIT", 1)
    transport = ASGITransport(app=app, client=("192.0.2.3", 5000))
    async with AsyncClient(transport=transport, base_url="https://test.example") as api:
        responses = await asyncio.gather(*(
            api.post("/api/v1/nodes/invites", json={"name": "test"}, headers=auth_headers)
            for _ in range(2)
        ))
        assert sorted(response.status_code for response in responses) == [200, 429]
        valid = next(response.json() for response in responses if response.status_code == 200)
        assert (await api.get("/api/v1/nodes/join", params={"token": valid["token"]})).status_code == 200


@pytest.mark.asyncio
async def test_global_failure_limit_and_safe_event(monkeypatch):
    monkeypatch.setattr(nodes, "NODE_REGISTRATION_FAILURE_LIMIT", 2)
    monkeypatch.setattr(nodes, "NODE_REGISTRATION_LOG_THRESHOLD", 2)
    events = []
    monkeypatch.setattr(nodes, "log_event", lambda *event: events.append(event))
    transport = ASGITransport(app=app, client=("127.0.0.1", 5000))
    async with AsyncClient(transport=transport, base_url="https://test.example") as api:
        for ip in ("192.0.2.10", "192.0.2.10", "192.0.2.11"):
            response = await api.get(
                "/api/v1/nodes/join", params={"token": "invalid"},
                headers={"X-Real-IP": ip},
            )
            assert response.status_code == (429 if ip.endswith(".11") else 401)
    assert len(events) == 1
    assert events[0][3] == {"attempts": 2}
