"""Выбор страны меняет серверный выход и сохраняет общий доступ и учёт трафика."""
import base64
import json
from urllib.parse import unquote, urlsplit
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.client import Client, ClientProfile
from app.models.node import Node
from app.models.setting import Setting
from app.services.client_service import ClientService
from app.services.subscription_routes import country_flag, read_routes, route_profile, profile_email, setting_key, traffic_email
from conftest import TestingSessionLocal
from test_client_exit import request


async def seed(kind="vless_reality_tcp"):
    async with TestingSessionLocal() as db:
        db.add_all([
            Node(id=1, name="Германия", host="192.0.2.1", port=443, protocol="trojan", secret="node-secret-de", country_code="DE"),
            Node(id=2, name="Нидерланды", host="192.0.2.2", port=443, protocol="trojan", secret="node-secret-nl", country_code="NL"),
            Node(id=3, name="Выключена", host="192.0.2.3", port=443, protocol="trojan", secret="node-secret-off", is_enabled=False),
            Setting(key="active_node_id", value="1"),
            Setting(key="profiles.enabled.awg", value="false"),
            Setting(key=f"profiles.enabled.{kind}", value="true"),
        ])
        client = Client(name="Клиент", phone="", email="", sub_token="countries-test", traffic_total=123)
        db.add(client)
        await db.flush()
        profile = ClientProfile(client_id=client.id, kind=kind, uuid="3be321a7-47ad-47ae-95bc-69ea7ba93e20" if kind.startswith("vless") else None,
                                auth="example-hysteria-password" if kind == "hysteria2" else None)
        db.add(profile)
        await db.commit()
        return client.id


async def subscription():
    response = await request("GET", "/api/v1/sub/countries-test?format=raw", {})
    assert response.status_code == 200, response.text
    return base64.b64decode(response.text).decode().splitlines()


@pytest.mark.asyncio
async def test_default_opt_in_and_disable_preserve_subscription(auth_headers):
    cid = await seed()
    path = f"/api/v1/clients/{cid}/subscription-routes"
    data = (await request("GET", path, auth_headers)).json()
    assert data["enabled"] is False and data["node_ids"] == []
    assert "node-secret" not in json.dumps(data) and "192.0.2." not in json.dumps(data)
    original = await subscription()
    assert len(original) == 1
    assert "🇩🇪" in unquote(urlsplit(original[0]).fragment)
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        response = await request("PUT", path, auth_headers, json={"enabled": True, "node_ids": [2]})
        assert response.status_code == 200, response.text
        sync.assert_awaited_once()
        links = await subscription()
        assert len(links) == 2
        assert urlsplit(links[0]).username == urlsplit(original[0]).username
        assert urlsplit(links[1]).username != urlsplit(original[0]).username
        assert urlsplit(links[1]).hostname == urlsplit(original[0]).hostname
        assert unquote(urlsplit(links[0]).fragment) == "🇩🇪 VLESS Reality TCP"
        assert unquote(urlsplit(links[1]).fragment) == "🇳🇱 VLESS Reality TCP"
        assert links == await subscription()
        assert (await request("PUT", path, auth_headers, json={"enabled": True, "node_ids": [2]})).status_code == 200
        sync.assert_awaited_once()
        response = await request("PUT", path, auth_headers, json={"enabled": False, "node_ids": [2]})
        assert response.status_code == 200
    assert await subscription() == original
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        assert client.sub_token == "countries-test" and client.traffic_total == 123 and client.exit_node_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["vless_reality_tcp", "vless_xhttp_reality", "vless_xhttp_tls", "hysteria2"])
async def test_routed_credentials_have_exact_outbound_and_respect_blocking(auth_headers, kind):
    cid = await seed(kind)
    async with TestingSessionLocal() as db:
        db.add(Setting(key=setting_key(cid), value=json.dumps({"enabled": True, "node_ids": [2, 3, 99]})))
        await db.commit()
    with patch("app.services.client_service.apply_reality_sni", new=AsyncMock()), \
         patch("app.services.xray.XrayService.apply_config", new=AsyncMock(return_value=(True, "ok"))) as apply:
        async with TestingSessionLocal() as db:
            ok, message = await ClientService.sync_xray_clients(db)
            assert ok, message
        options = apply.call_args.kwargs["profile_options"]
        arrays = {"vless_reality_tcp": apply.call_args.kwargs["clients"],
                  "vless_xhttp_reality": options["vless_xhttp_reality_clients"],
                  "vless_xhttp_tls": options["vless_xhttp_tls_clients"], "hysteria2": options["hysteria2_clients"]}
        credentials = arrays[kind]
        assert len(credentials) == 4
        async with TestingSessionLocal() as db:
            profile = (await db.execute(select(ClientProfile).where(ClientProfile.kind == kind))).scalar_one()
            routed = route_profile(profile, 2)
        email = profile_email(routed, cid)
        assert any(row["email"] == email and row.get("id", row.get("auth")) == (routed.uuid or routed.auth) for row in credentials)
        rules = options["routing_rules"]
        assert any(email in row.get("user", []) and row["outboundTag"] == "node-2" for row in rules)
        for node_id in (3, 99):
            assert any(f"c{cid}-{kind}-node{node_id}@md-next" in row.get("user", []) and row["outboundTag"] == "block" for row in rules)
    links = await subscription()
    assert sum("🇳🇱" in unquote(link) for link in links) == 1
    assert not any("Выключена" in unquote(link) for link in links)


@pytest.mark.asyncio
async def test_validation_permissions_and_rollback(auth_headers):
    from app.api.auth import create_access_token
    cid = await seed()
    path = f"/api/v1/clients/{cid}/subscription-routes"
    for body, code in [({"enabled": True, "node_ids": []}, 422), ({"enabled": True, "node_ids": [2, 2]}, 422),
                       ({"enabled": True, "node_ids": [True]}, 422), ({"enabled": True, "node_ids": ["2"]}, 422),
                       ({"enabled": True, "node_ids": [99]}, 404), ({"enabled": True, "node_ids": [3]}, 422)]:
        assert (await request("PUT", path, auth_headers, json=body)).status_code == code
    with patch("app.api.clients._sync_protocols", new=AsyncMock(side_effect=RuntimeError("failed"))), \
         patch("app.services.client_service.ClientService.restore_committed_configs", new=AsyncMock()) as restore:
        assert (await request("PUT", path, auth_headers, json={"enabled": True, "node_ids": [2]})).status_code == 502
        restore.assert_awaited_once()
    assert (await request("GET", path, auth_headers)).json()["enabled"] is False
    response = await request("POST", "/api/v1/users", auth_headers,
                             json={"username": "route-issuer", "password": "ExampleIssuerPassword!", "can_issue_subscriptions": True})
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "route-issuer"})}
    assert (await request("GET", path, headers)).status_code == 403
    assert (await request("PUT", path, headers, json={"enabled": True, "node_ids": [2]})).status_code == 403


def test_credentials_rotate_and_traffic_maps_to_original_profile():
    from types import SimpleNamespace
    base = SimpleNamespace(id=8, kind="vless_reality_tcp", uuid="first-secret", auth=None, is_enabled=True)
    first = route_profile(base, 2)
    assert first == route_profile(base, 2)
    assert first.uuid != route_profile(base, 3).uuid
    base.uuid = "second-secret"
    assert first.uuid != route_profile(base, 2).uuid
    assert traffic_email("c7-vless_reality_tcp-node2@md-next") == "c7-vless_reality_tcp@md-next"
    assert traffic_email("c7-cdn-node2@md-next") == "c7-cdn@md-next"
    assert read_routes({}, 7) == {"enabled": False, "node_ids": []}


@pytest.mark.parametrize("code, expected", [("NL", "🇳🇱"), ("de", "🇩🇪"), (None, "🌐"), ("XX1", "🌐"), ("РФ", "🌐")])
def test_country_flags(code, expected):
    assert country_flag(code) == expected


@pytest.mark.asyncio
async def test_additional_route_traffic_counts_towards_common_quota(auth_headers, monkeypatch):
    from app.services import traffic_collector as collector, client_activity
    cid = await seed()
    monkeypatch.setattr(collector, "AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(collector, "LIMITS_SYNC_PENDING", False)
    monkeypatch.setattr(client_activity, "_seen", {})
    monkeypatch.setattr(client_activity, "_sources", {})
    payload = {"stat": [
        {"name": f"user>>>c{cid}-vless_reality_tcp@md-next>>>traffic>>>uplink", "value": 100},
        {"name": f"user>>>c{cid}-vless_reality_tcp-node2@md-next>>>traffic>>>uplink", "value": 200},
        {"name": f"user>>>c{cid}-vless_reality_tcp-node2@md-next>>>traffic>>>downlink", "value": 50},
    ]}
    async with TestingSessionLocal() as db:
        (await db.get(Client, cid)).monthly_traffic_limit = 300
        await db.commit()
    with patch.object(collector, "_run_command", new=AsyncMock(side_effect=[json.dumps(payload), ""])), \
         patch.object(ClientService, "sync_xray_clients", new=AsyncMock(return_value=(True, "ok"))), \
         patch("app.services.awg.AWGService.sync_server_config", new=AsyncMock(return_value=(True, "ok"))):
        await collector._collect_client_traffic()
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        assert client.traffic_total == 350
        assert client.monthly_traffic_up == 300 and client.monthly_traffic_down == 50
    assert await subscription() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_node_delete_updates_route_choices_atomically(auth_headers, ok):
    cid = await seed()
    async with TestingSessionLocal() as db:
        db.add(Setting(key=setting_key(cid), value=json.dumps({"enabled": True, "node_ids": [2]})))
        await db.commit()
    with patch.object(ClientService, "sync_xray_clients", new=AsyncMock(return_value=(ok, "result"))), \
         patch.object(ClientService, "restore_committed_configs", new=AsyncMock()):
        response = await request("DELETE", "/api/v1/nodes/2", auth_headers)
        assert response.status_code == (204 if ok else 502), response.text
    data = (await request("GET", f"/api/v1/clients/{cid}/subscription-routes", auth_headers)).json()
    assert data["enabled"] is (not ok)
    assert data["node_ids"] == ([] if ok else [2])


@pytest.mark.asyncio
async def test_client_deletion_removes_optional_settings(auth_headers):
    cid = await seed()
    async with TestingSessionLocal() as db:
        db.add(Setting(key=setting_key(cid), value=json.dumps({"enabled": True, "node_ids": [2]})))
        await db.commit()
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        assert (await request("DELETE", f"/api/v1/clients/{cid}", auth_headers)).status_code == 204
    async with TestingSessionLocal() as db:
        assert await db.get(Setting, setting_key(cid)) is None
