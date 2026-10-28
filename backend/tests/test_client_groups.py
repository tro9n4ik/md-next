from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.models.client import Client, ClientProfile
from app.models.user import User
from app.api.auth import create_access_token
from app.services import client_activity
from conftest import TestingSessionLocal


async def request(method, path, headers, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        return await api.request(method, path, headers=headers, **kwargs)


async def make_group(headers, name="Друзья", color="emerald"):
    result = await request("POST", "/api/v1/client-groups", headers, json={"name": name, "color": color})
    assert result.status_code == 201, result.text
    return result.json()["id"]


async def make_client(headers, **data):
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        result = await request("POST", "/api/v1/clients", headers, json={"name": "Тест", **data})
    assert result.status_code == 201, result.text
    return result.json()["client"]["id"]


async def issuer(admin, can_issue=True, view_all=False):
    result = await request("POST", "/api/v1/users", admin, json={"username": "issuer", "password": "SubadminPassword123!", "can_issue_subscriptions": can_issue, "view_all_clients": view_all})
    assert result.status_code == 201, result.text
    return result.json()["id"], {"Authorization": "Bearer " + create_access_token({"sub": "issuer"})}


@pytest.mark.asyncio
async def test_groups_filter_and_delete_preserve_subscription(auth_headers):
    gid = await make_group(auth_headers)
    cid = await make_client(auth_headers, group_id=gid)
    other = await make_client(auth_headers)
    async with TestingSessionLocal() as db:
        before = (await db.get(Client, cid)).sub_token
    grouped = (await request("GET", f"/api/v1/clients?group_id={gid}", auth_headers)).json()
    assert [row["id"] for row in grouped] == [cid]
    assert grouped[0]["group"]["name"] == "Друзья"
    assert [row["id"] for row in (await request("GET", "/api/v1/clients?group_id=0", auth_headers)).json()] == [other]
    summary = (await request("GET", "/api/v1/client-groups", auth_headers)).json()
    assert summary["groups"][0]["count"] == 1 and summary["ungrouped_count"] == 1 and summary["total"] == 2
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        assert (await request("DELETE", f"/api/v1/client-groups/{gid}", auth_headers)).status_code == 204
        sync.assert_not_awaited()
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        assert client.group_id is None and client.sub_token == before and client.is_active


@pytest.mark.asyncio
async def test_group_validation_rename_and_uniqueness(auth_headers):
    gid = await make_group(auth_headers, "  Друзья  ")
    duplicate = await request("POST", "/api/v1/client-groups", auth_headers, json={"name": "ДРУЗЬЯ"})
    assert duplicate.status_code == 409
    for data in [{"name": " "}, {"name": "a" * 49}, {"name": "Test", "color": "url(evil)"}]:
        assert (await request("POST", "/api/v1/client-groups", auth_headers, json=data)).status_code == 422
    response = await request("PUT", f"/api/v1/client-groups/{gid}", auth_headers, json={"name": "Близкие друзья", "color": "blue"})
    assert response.status_code == 200 and response.json()["color"] == "blue"


@pytest.mark.asyncio
async def test_subadmin_groups_do_not_reveal_other_client_counts(auth_headers):
    gid = await make_group(auth_headers)
    foreign = await make_client(auth_headers, group_id=gid)
    _, headers = await issuer(auth_headers)
    own = await make_client(headers)
    summary = (await request("GET", "/api/v1/client-groups", headers)).json()
    assert summary["groups"][0]["count"] == 0 and summary["total"] == 1
    assert (await request("GET", f"/api/v1/clients?group_id={gid}", headers)).json() == []
    assert (await request("PUT", f"/api/v1/clients/{foreign}/group", headers, json={"group_id": gid})).status_code == 404
    assert (await request("PUT", f"/api/v1/clients/{own}/group", headers, json={"group_id": gid})).status_code == 200
    for method, path, body in [("POST", "/api/v1/client-groups", {"name": "Private"}), ("PUT", f"/api/v1/client-groups/{gid}", {"name": "Other"}), ("DELETE", f"/api/v1/client-groups/{gid}", None)]:
        assert (await request(method, path, headers, **({"json": body} if body else {}))).status_code == 403


@pytest.mark.asyncio
async def test_view_all_does_not_grant_foreign_group_edit(auth_headers):
    gid = await make_group(auth_headers)
    foreign = await make_client(auth_headers)
    _, headers = await issuer(auth_headers, view_all=True)
    found = (await request("GET", "/api/v1/clients", headers)).json()
    assert found[0]["id"] == foreign and not found[0]["can_change_group"]
    assert (await request("PUT", f"/api/v1/clients/{foreign}/group", headers, json={"group_id": gid})).status_code == 404


@pytest.mark.asyncio
async def test_read_only_subadmin_cannot_assign_groups(auth_headers):
    gid = await make_group(auth_headers)
    uid, headers = await issuer(auth_headers, can_issue=False)
    async with TestingSessionLocal() as db:
        own = Client(name="Own", phone="", email="", created_by_id=uid)
        db.add(own)
        await db.commit()
        cid = own.id
    assert (await request("PUT", f"/api/v1/clients/{cid}/group", headers, json={"group_id": gid})).status_code == 403


@pytest.mark.asyncio
async def test_assignment_validation_and_no_protocol_sync(auth_headers):
    gid = await make_group(auth_headers)
    cid = await make_client(auth_headers)
    for body, code in [({}, 422), ({"group_id": -1}, 422), ({"group_id": 99999}, 404), ({"group_id": gid, "is_active": False}, 422)]:
        assert (await request("PUT", f"/api/v1/clients/{cid}/group", auth_headers, json=body)).status_code == code
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        for value in (gid, None):
            assert (await request("PUT", f"/api/v1/clients/{cid}/group", auth_headers, json={"group_id": value})).status_code == 200
        sync.assert_not_awaited()
    assert (await request("POST", "/api/v1/clients", auth_headers, json={"name": "Missing", "group_id": 99999})).status_code == 404


@pytest.mark.asyncio
async def test_last_seen_survives_missing_telemetry(auth_headers, monkeypatch):
    monkeypatch.setattr(client_activity, "_seen", {})
    monkeypatch.setattr(client_activity, "_sources", {})
    stamp = datetime(2026, 10, 28, 7, 35, tzinfo=timezone.utc)
    async with TestingSessionLocal() as db:
        client = Client(name="Seen", phone="", email="", last_seen_at=stamp)
        db.add(client)
        await db.flush()
        db.add(ClientProfile(client_id=client.id, kind="vless_reality_tcp"))
        await db.commit()
    result = (await request("GET", "/api/v1/clients", auth_headers)).json()[0]
    assert result["last_seen_at"] == stamp.isoformat()
    assert result["connection_status"] == "unknown"


@pytest.mark.asyncio
async def test_collector_only_updates_last_seen_on_positive_traffic(auth_headers):
    cid = await make_client(auth_headers)
    from app.services import traffic_collector as collector
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        client.last_seen_at = old
        await db.commit()
    from sqlalchemy import select
    async with TestingSessionLocal() as db:
        kind = (await db.execute(select(ClientProfile.kind).where(ClientProfile.client_id == cid, ClientProfile.kind != "awg"))).scalars().first()
    assert kind
    for amount in (0, 100):
        import json
        output = json.dumps({"stat": [{"name": f"user>>>c{cid}-{kind}@md-next>>>traffic>>>uplink", "value": amount}]})
        with patch.object(collector, "AsyncSessionLocal", TestingSessionLocal), patch.object(collector, "_run_command", new=AsyncMock(side_effect=[output, ""])):
            await collector._collect_client_traffic()
        async with TestingSessionLocal() as db:
            stamp = (await db.get(Client, cid)).last_seen_at
            assert (stamp.replace(tzinfo=timezone.utc) == old) if amount == 0 else (stamp.replace(tzinfo=timezone.utc) > old)
