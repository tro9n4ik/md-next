"""Разрешения выдачи и удаления проверяются API независимо от интерфейса."""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.auth import create_access_token
from app.models.client import Client
from app.models.user import User
from conftest import TestingSessionLocal
from test_subadmins import account, client, request


async def fresh_headers(uid):
    async with TestingSessionLocal() as db:
        user = await db.get(User, uid)
        return {"Authorization": "Bearer " + create_access_token({"sub": user.username, "token_version": user.token_version})}


@pytest.mark.asyncio
async def test_revoke_issuing_blocks_creation_and_credentials_but_preserves_clients(auth_headers):
    uid, issuer = await account(auth_headers)
    own = await client(issuer)
    assert (await request("GET", f"/api/v1/clients/{own}/profiles", issuer)).status_code == 200
    result = await request("PUT", f"/api/v1/users/{uid}", auth_headers, json={"can_issue_subscriptions": False})
    assert result.status_code == 200 and result.json()["can_issue_subscriptions"] is False
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 401
    issuer = await fresh_headers(uid)
    me = (await request("GET", "/api/v1/auth/me", issuer)).json()
    assert me["can_issue_subscriptions"] is False and me["can_delete_subscriptions"] is False
    assert [c["id"] for c in (await request("GET", "/api/v1/clients", issuer)).json()] == [own]
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        assert (await request("POST", "/api/v1/clients", issuer, json={"name": "Forbidden"})).status_code == 403
        assert (await request("GET", f"/api/v1/clients/{own}/profiles", issuer)).status_code == 403
        sync.assert_not_awaited()
    # Публичные ранее выданные ссылки сохраняются, отзыв касается прав оператора.
    async with TestingSessionLocal() as db:
        stored = await db.get(Client, own)
        assert stored.is_active and stored.sub_token
    await request("PUT", f"/api/v1/users/{uid}", auth_headers, json={"can_issue_subscriptions": True})
    assert (await request("GET", f"/api/v1/clients/{own}/profiles", await fresh_headers(uid))).status_code == 200


@pytest.mark.asyncio
async def test_delete_permission_only_allows_own_clients_even_with_global_visibility(auth_headers):
    uid, issuer = await account(auth_headers, view_all=True, can_delete_subscriptions=True)
    own = await client(issuer, "Own")
    foreign = await client(auth_headers, "Foreign")
    rows = (await request("GET", "/api/v1/clients", issuer)).json()
    assert {c["id"]: c["can_delete"] for c in rows} == {own: True, foreign: False}
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        for cid in (foreign, 99999):
            assert (await request("DELETE", f"/api/v1/clients/{cid}", issuer)).status_code == 404
        sync.assert_not_awaited()
        assert (await request("DELETE", f"/api/v1/clients/{own}", issuer)).status_code == 204
        sync.assert_awaited_once()
    async with TestingSessionLocal() as db:
        assert await db.get(Client, own) is None
        assert await db.get(Client, foreign) is not None
    assert (await request("PUT", "/api/v1/users/1", issuer, json={"can_delete_subscriptions": True})).status_code == 403
    assert (await request("PUT", f"/api/v1/clients/{foreign}", issuer, json={"is_active": False})).status_code == 403


@pytest.mark.asyncio
async def test_delete_permission_is_independent_from_issuing_and_revocable(auth_headers):
    uid, issuer = await account(auth_headers, can_delete_subscriptions=True)
    own = await client(issuer)
    await request("PUT", f"/api/v1/users/{uid}", auth_headers, json={"can_issue_subscriptions": False})
    issuer = await fresh_headers(uid)
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        assert (await request("DELETE", f"/api/v1/clients/{own}", issuer)).status_code == 204
    await request("PUT", f"/api/v1/users/{uid}", auth_headers, json={"can_delete_subscriptions": False})
    assert (await request("DELETE", "/api/v1/clients/99999", issuer)).status_code == 401
    issuer = await fresh_headers(uid)
    assert (await request("DELETE", "/api/v1/clients/99999", issuer)).status_code == 403


@pytest.mark.asyncio
async def test_delete_failure_restores_client_and_hides_server_details(auth_headers):
    _, issuer = await account(auth_headers, can_delete_subscriptions=True)
    own = await client(issuer)
    with patch("app.api.clients._sync_protocols", side_effect=RuntimeError("private-config-detail")), patch("app.services.client_service.ClientService.restore_committed_configs", new=AsyncMock()) as restore:
        result = await request("DELETE", f"/api/v1/clients/{own}", issuer)
        assert result.status_code == 502 and "private-config-detail" not in result.text
        restore.assert_awaited_once()
    async with TestingSessionLocal() as db:
        assert await db.get(Client, own) is not None


@pytest.mark.asyncio
async def test_admin_has_effective_permissions_and_subadmin_cannot_self_grant(auth_headers):
    _, issuer = await account(auth_headers, can_issue_subscriptions=False)
    me = (await request("GET", "/api/v1/auth/me", auth_headers)).json()
    assert me["can_issue_subscriptions"] and me["can_delete_subscriptions"]
    forged = {"Authorization": "Bearer " + create_access_token({"sub": "issuer", "can_issue_subscriptions": True, "can_delete_subscriptions": True})}
    assert (await request("POST", "/api/v1/clients", forged, json={"name": "Forbidden"})).status_code == 403
    assert (await request("DELETE", "/api/v1/clients/1", forged)).status_code == 403
    assert (await request("PUT", "/api/v1/users/2", issuer, json={"can_issue_subscriptions": True})).status_code == 403


@pytest.mark.asyncio
async def test_pause_permission_only_changes_status_of_own_clients(auth_headers):
    uid, issuer = await account(auth_headers, view_all=True, can_pause_subscriptions=True)
    own = await client(issuer, "Own", monthly_traffic_limit=12345, subscription_period="month")
    foreign = await client(auth_headers, "Foreign")
    async with TestingSessionLocal() as db:
        stored = await db.get(Client, own)
        before = (stored.sub_token, stored.expires_at, stored.monthly_traffic_limit)
    rows = (await request("GET", "/api/v1/clients", issuer)).json()
    assert {c["id"]: c["can_pause"] for c in rows} == {own: True, foreign: False}
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        for cid in (foreign, 99999):
            assert (await request("PUT", f"/api/v1/clients/{cid}/status", issuer, json={"is_active": False})).status_code == 404
        sync.assert_not_awaited()
        for active in (False, True):
            result = await request("PUT", f"/api/v1/clients/{own}/status", issuer, json={"is_active": active})
            assert result.status_code == 200 and result.json()["is_active"] == active
            async with TestingSessionLocal() as db:
                stored = await db.get(Client, own)
                assert stored.is_active == active
                assert (stored.sub_token, stored.expires_at, stored.monthly_traffic_limit) == before
        assert sync.await_count == 2
        assert (await request("PUT", f"/api/v1/clients/{own}", issuer, json={"monthly_traffic_limit": 0})).status_code == 403


@pytest.mark.asyncio
async def test_pause_permission_is_independent_and_revokes_existing_tokens(auth_headers):
    uid, issuer = await account(auth_headers)
    own = await client(issuer)
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        assert (await request("PUT", f"/api/v1/clients/{own}/status", issuer, json={"is_active": False})).status_code == 403
        sync.assert_not_awaited()
    await request("PUT", f"/api/v1/users/{uid}", auth_headers, json={"can_pause_subscriptions": True, "can_issue_subscriptions": False})
    assert (await request("PUT", f"/api/v1/clients/{own}/status", issuer, json={"is_active": False})).status_code == 401
    issuer = await fresh_headers(uid)
    me = (await request("GET", "/api/v1/auth/me", issuer)).json()
    assert me["can_pause_subscriptions"] is True and me["can_issue_subscriptions"] is False
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        assert (await request("PUT", f"/api/v1/clients/{own}/status", issuer, json={"is_active": False})).status_code == 200
    await request("PUT", f"/api/v1/users/{uid}", auth_headers, json={"can_pause_subscriptions": False})
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 401
    issuer = await fresh_headers(uid)
    assert (await request("PUT", f"/api/v1/clients/{own}/status", issuer, json={"is_active": True})).status_code == 403
    assert (await request("PUT", f"/api/v1/users/{uid}", issuer, json={"can_pause_subscriptions": True})).status_code == 403


@pytest.mark.asyncio
async def test_pause_rejects_extra_fields_and_non_boolean_values(auth_headers):
    _, issuer = await account(auth_headers, can_pause_subscriptions=True)
    own = await client(issuer)
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        for body in ({"is_active": False, "monthly_traffic_limit": 0}, {"is_active": "false"}, {}):
            assert (await request("PUT", f"/api/v1/clients/{own}/status", issuer, json=body)).status_code == 422
        sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_pause_failure_restores_status_without_leaking_config(auth_headers):
    _, issuer = await account(auth_headers, can_pause_subscriptions=True)
    own = await client(issuer)
    with patch("app.api.clients._sync_protocols", side_effect=RuntimeError("private-config-detail")), \
         patch("app.services.client_service.ClientService.restore_committed_configs", new=AsyncMock()) as restore:
        result = await request("PUT", f"/api/v1/clients/{own}/status", issuer, json={"is_active": False})
        assert result.status_code == 502 and "private-config-detail" not in result.text
        restore.assert_awaited_once()
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, own)).is_active is True


@pytest.mark.asyncio
async def test_admin_can_pause_other_owners_and_repeated_status_is_noop(auth_headers):
    _, issuer = await account(auth_headers)
    own = await client(issuer)
    assert (await request("GET", "/api/v1/auth/me", auth_headers)).json()["can_pause_subscriptions"] is True
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        for _ in range(2):
            assert (await request("PUT", f"/api/v1/clients/{own}/status", auth_headers, json={"is_active": False})).status_code == 200
        sync.assert_awaited_once()
