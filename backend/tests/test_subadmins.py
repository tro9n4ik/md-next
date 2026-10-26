"""Security boundaries for accounts that create and issue client profiles."""

import re
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.api.auth import create_access_token, require_admin, verify_password
from app.main import app
from app.models.client import Client
from app.models.user import User
from conftest import TestingSessionLocal


async def request(method, path, headers, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as api:
        return await api.request(method, path, headers=headers, **kwargs)


async def account(admin, name="issuer", view_all=False):
    response = await request(
        "POST",
        "/api/v1/users",
        admin,
        json={
            "username": name,
            "password": "SubadminPassword123!",
            "view_all_clients": view_all,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], {
        "Authorization": "Bearer " + create_access_token({"sub": name})
    }


async def client(headers, name="Client", **kwargs):
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        result = await request(
            "POST", "/api/v1/clients", headers, json={"name": name, **kwargs}
        )
    assert result.status_code == 201, result.text
    return result.json()["client"]["id"]


@pytest.mark.asyncio
async def test_account_creation_login_and_safe_response(auth_headers):
    uid, headers = await account(auth_headers)
    result = await request(
        "POST",
        "/api/v1/auth/login",
        {},
        json={"username": "issuer", "password": "SubadminPassword123!"},
    )
    assert result.status_code == 200 and result.json()["access_token"]
    me = (await request("GET", "/api/v1/auth/me", headers)).json()
    assert me["role"] == "subadmin" and me["view_all_clients"] is False
    data = (await request("GET", "/api/v1/users", auth_headers)).json()
    assert [u["id"] for u in data] == [uid]
    assert not (
        {
            "hashed_password",
            "password",
            "totp_secret",
            "totp_pending_secret",
            "token_version",
        }
        & set(data[0])
    )
    async with TestingSessionLocal() as db:
        user = await db.get(User, uid)
        assert user.hashed_password != "SubadminPassword123!"
        assert verify_password("SubadminPassword123!", user.hashed_password)


@pytest.mark.asyncio
async def test_own_clients_and_profiles_are_isolated(auth_headers):
    uid, issuer = await account(auth_headers)
    other_id, other = await account(auth_headers, "other")
    admin_client = await client(auth_headers, "Admin")
    own = await client(issuer, "Own", created_by_id=other_id)
    foreign = await client(other, "Foreign")
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, own)).created_by_id == uid
        db.add(Client(name="Legacy", phone="", email=""))
        await db.commit()
    for query in ("", "?q=Admin", "?status=active&sort=name&order=asc"):
        found = (await request("GET", "/api/v1/clients" + query, issuer)).json()
        assert {row["id"] for row in found} <= {own}
    profiles = await request("GET", f"/api/v1/clients/{own}/profiles", issuer)
    assert (
        profiles.status_code == 200
        and profiles.json()["subscription_url"]
        and profiles.json()["profiles"]
    )
    for cid in (foreign, admin_client, 99999):
        result = await request("GET", f"/api/v1/clients/{cid}/profiles", issuer)
        assert result.status_code == 404 and "subscription_url" not in result.json()
    assert len((await request("GET", "/api/v1/clients", auth_headers)).json()) == 4


def contains_dependency(dependency, call):
    return dependency.call is call or any(
        contains_dependency(child, call) for child in dependency.dependencies
    )


@pytest.mark.asyncio
async def test_subadmin_cannot_call_any_panel_administration_endpoint(auth_headers):
    _, issuer = await account(auth_headers, view_all=True)
    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/"):
            continue
        if route.path.startswith(("/api/v1/auth/", "/api/v1/sub/")) or route.path in {
            "/api/v1/nodes/register",
            "/api/v1/nodes/join",
        }:
            continue
        if route.path == "/api/v1/clients" or (
            route.path.endswith("/profiles") and route.methods == {"GET"}
        ):
            continue
        assert contains_dependency(route.dependant, require_admin), route.path
        routes.append(route)
    assert len(routes) > 50
    for route in routes:
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            result = await request(
                method,
                path,
                issuer,
                **({"json": {}} if method in {"POST", "PUT", "PATCH"} else {}),
            )
            assert result.status_code == 403, (method, path, result.text)


@pytest.mark.asyncio
async def test_visibility_switch_controls_listing_and_issuing_but_not_mutation(
    auth_headers,
):
    uid, issuer = await account(auth_headers)
    foreign = await client(auth_headers)
    assert (await request("GET", "/api/v1/clients", issuer)).json() == []
    result = await request(
        "PUT", f"/api/v1/users/{uid}", auth_headers, json={"view_all_clients": True}
    )
    assert result.status_code == 200 and result.json()["view_all_clients"] is True
    assert (await request("GET", "/api/v1/clients", issuer)).status_code == 401
    async with TestingSessionLocal() as db:
        version = (await db.get(User, uid)).token_version
    fresh = {
        "Authorization": "Bearer "
        + create_access_token({"sub": "issuer", "token_version": version})
    }
    assert [
        row["id"] for row in (await request("GET", "/api/v1/clients", fresh)).json()
    ] == [foreign]
    assert (
        await request("GET", f"/api/v1/clients/{foreign}/profiles", fresh)
    ).status_code == 200
    for method, suffix, body in [
        ("DELETE", "", None),
        ("PUT", "/access", {"profiles": {}}),
    ]:
        assert (
            await request(
                method,
                f"/api/v1/clients/{foreign}{suffix}",
                fresh,
                **({"json": body} if body else {}),
            )
        ).status_code == 403
    await request(
        "PUT", f"/api/v1/users/{uid}", auth_headers, json={"view_all_clients": False}
    )
    async with TestingSessionLocal() as db:
        version = (await db.get(User, uid)).token_version
    restricted = {
        "Authorization": "Bearer "
        + create_access_token({"sub": "issuer", "token_version": version})
    }
    assert (await request("GET", "/api/v1/clients", restricted)).json() == []
    assert (
        await request("GET", f"/api/v1/clients/{foreign}/profiles", restricted)
    ).status_code == 404


@pytest.mark.asyncio
async def test_disable_and_reenable_never_revives_old_session(auth_headers):
    uid, issuer = await account(auth_headers)
    assert (
        await request(
            "PUT", f"/api/v1/users/{uid}", auth_headers, json={"is_active": False}
        )
    ).status_code == 200
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 401
    assert (
        await request(
            "POST",
            "/api/v1/auth/login",
            {},
            json={"username": "issuer", "password": "SubadminPassword123!"},
        )
    ).status_code == 401
    assert (
        await request(
            "PUT", f"/api/v1/users/{uid}", auth_headers, json={"is_active": True}
        )
    ).status_code == 200
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 401
    assert (
        await request(
            "POST",
            "/api/v1/auth/login",
            {},
            json={"username": "issuer", "password": "SubadminPassword123!"},
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_revokes_session_and_keeps_clients(auth_headers):
    uid, issuer = await account(auth_headers)
    cid = await client(issuer)
    assert (
        await request(
            "PUT",
            f"/api/v1/users/{uid}",
            auth_headers,
            json={"password": "ResetPassword123!"},
        )
    ).status_code == 200
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 401
    assert (
        await request(
            "POST",
            "/api/v1/auth/login",
            {},
            json={"username": "issuer", "password": "SubadminPassword123!"},
        )
    ).status_code == 401
    assert (
        await request(
            "POST",
            "/api/v1/auth/login",
            {},
            json={"username": "issuer", "password": "ResetPassword123!"},
        )
    ).status_code == 200
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, cid)).created_by_id == uid


@pytest.mark.asyncio
async def test_account_api_cannot_create_admin_or_modify_primary_admin(auth_headers):
    assert (
        await request(
            "POST",
            "/api/v1/users",
            auth_headers,
            json={
                "username": "escalate",
                "password": "Password12345!",
                "role": "admin",
            },
        )
    ).status_code == 422
    await account(auth_headers)
    for name in ("issuer", "admin"):
        assert (
            await request(
                "POST",
                "/api/v1/users",
                auth_headers,
                json={"username": name, "password": "Password12345!"},
            )
        ).status_code == 409
    assert (
        await request("PUT", "/api/v1/users/1", auth_headers, json={"is_active": False})
    ).status_code == 404
    assert (await request("GET", "/api/v1/auth/me", auth_headers)).status_code == 200


@pytest.mark.asyncio
async def test_jwt_role_claim_cannot_grant_permissions(auth_headers):
    _, issuer = await account(auth_headers)
    forged = {
        "Authorization": "Bearer "
        + create_access_token(
            {"sub": "issuer", "role": "admin", "view_all_clients": True}
        )
    }
    assert (await request("GET", "/api/v1/users", forged)).status_code == 403
    assert (
        await request("GET", "/api/v1/settings/telegram", issuer)
    ).status_code == 403
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 200


@pytest.mark.asyncio
async def test_subadmin_can_change_own_password_and_setup_2fa(auth_headers):
    _, issuer = await account(auth_headers)
    response = await request("POST", "/api/v1/auth/2fa/setup", issuer, json={})
    assert response.status_code == 200 and response.json()["secret"]
    response = await request(
        "PUT",
        "/api/v1/auth/password",
        issuer,
        json={
            "old_password": "SubadminPassword123!",
            "new_password": "ChangedPassword123!",
        },
    )
    assert response.status_code == 200
    assert (await request("GET", "/api/v1/auth/me", issuer)).status_code == 401


@pytest.mark.asyncio
async def test_creation_failure_hides_internal_error_and_rolls_back(auth_headers):
    uid, issuer = await account(auth_headers)
    with patch(
        "app.api.clients._sync_protocols",
        side_effect=RuntimeError("internal-secret-settings"),
    ), patch(
        "app.services.client_service.ClientService.restore_committed_configs",
        new=AsyncMock(),
    ):
        result = await request(
            "POST", "/api/v1/clients", issuer, json={"name": "Failed"}
        )
    assert result.status_code == 502 and "internal-secret-settings" not in result.text
    async with TestingSessionLocal() as db:
        assert (
            not (await db.execute(select(Client).where(Client.created_by_id == uid)))
            .scalars()
            .all()
        )
