"""Индивидуальные маршруты, сохранение подписки и закрытый отказ выхода."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.main import app
from app.models.client import Client, ClientProfile
from app.models.node import Node
from app.models.setting import Setting
from app.services.client_exit import build_client_exit_rules
from conftest import TestingSessionLocal


async def request(method, path, headers, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        return await api.request(method, path, headers=headers, **kwargs)


async def seed():
    async with TestingSessionLocal() as db:
        db.add_all([Node(id=1, name="Германия", host="192.0.2.10", port=24443, protocol="trojan", secret="example-node-one", country_code="DE"),
                    Node(id=2, name="Нидерланды", host="192.0.2.20", port=24443, protocol="trojan", secret="example-node-two", country_code="NL"),
                    Node(id=3, name="Выключена", host="192.0.2.30", port=24443, protocol="trojan", secret="example-node-three", is_enabled=False),
                    Setting(key="active_node_id", value="1")])
        client = Client(name="Ирина", phone="", email="", monthly_traffic_limit=10**9, monthly_traffic_up=123,
                        monthly_traffic_down=456, traffic_total=579, sub_token="same-subscription", created_at=datetime.now(timezone.utc))
        db.add(client)
        await db.flush()
        db.add(ClientProfile(client_id=client.id, kind="vless_reality_tcp", uuid="same-profile-id"))
        await db.commit()
        return client.id


@pytest.mark.asyncio
async def test_assign_reset_and_common_switch_preserve_identity(auth_headers):
    cid = await seed()
    path = f"/api/v1/clients/{cid}/exit-route"
    with patch("app.api.clients._sync_protocols", new=AsyncMock()) as sync:
        response = await request("PUT", path, auth_headers, json={"node_id": 2})
        assert response.status_code == 200, response.text
        assert response.json()["node"]["name"] == "Нидерланды"
        sync.assert_awaited_once()
        # Повтор того же выбора не перезапускает конфигурацию.
        assert (await request("PUT", path, auth_headers, json={"node_id": 2})).status_code == 200
        sync.assert_awaited_once()
        async with TestingSessionLocal() as db:
            (await db.get(Setting, "active_node_id")).value = "direct:manual"
            await db.commit()
        data = (await request("GET", path, auth_headers)).json()
        assert data["route"]["node_id"] == 2 and data["common"]["mode"] == "direct"
        assert (await request("GET", "/api/v1/clients", auth_headers)).json()[0]["exit_route"]["node_id"] == 2
        assert (await request("PUT", path, auth_headers, json={"node_id": None})).json()["status"] == "direct"
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        profile = (await db.execute(select(ClientProfile))).scalar_one()
        assert client.exit_node_id is None and client.sub_token == "same-subscription"
        assert client.monthly_traffic_up == 123 and client.monthly_traffic_down == 456 and client.traffic_total == 579
        assert profile.uuid == "same-profile-id" and client.monthly_traffic_limit == 10**9


@pytest.mark.asyncio
async def test_options_counts_no_node_credentials(auth_headers):
    cid = await seed()
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        await request("PUT", f"/api/v1/clients/{cid}/exit-route", auth_headers, json={"node_id": 2})
    response = await request("GET", "/api/v1/clients/exit-options", auth_headers)
    data = response.json()
    assert data["nodes"][1]["assigned_clients"] == 1 and data["common_clients"] == 0
    assert len((await request("GET", "/api/v1/clients?exit_node_id=2", auth_headers)).json()) == 1
    assert (await request("GET", "/api/v1/clients?exit_node_id=0", auth_headers)).json() == []
    assert not data["nodes"][2]["selectable"]
    assert all(secret not in response.text for secret in ["192.0.2.", "example-node-", "public_key", "short_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("apply_ok", [True, False])
async def test_delete_active_node_clears_links_and_switches_atomically(auth_headers, monkeypatch, apply_ok):
    from app.db.database import get_db
    from app.models.node_invite import NodeInvite

    async def database_with_foreign_keys():
        async with TestingSessionLocal() as db:
            await db.execute(text("PRAGMA foreign_keys=ON"))
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, database_with_foreign_keys)
    cid = await seed()
    async with TestingSessionLocal() as db:
        (await db.get(Client, cid)).exit_node_id = 1
        (await db.get(Node, 1)).priority = 0
        (await db.get(Node, 2)).priority = 1
        (await db.get(Node, 3)).priority = 2
        db.add_all([
            NodeInvite(id=1, name="used", token_hash="synthetic-hash",
                       expires_at=datetime.now(timezone.utc), node_id=1),
            Setting(key="warp.node_id", value="1"),
        ])
        await db.commit()

    async def apply(db, *, active_node):
        assert await db.scalar(text("PRAGMA foreign_keys")) == 1
        assert active_node.id == 2
        assert (await db.get(Client, cid)).exit_node_id is None
        return apply_ok, "test result"

    with patch("app.api.nodes.ClientService.sync_xray_clients", new=AsyncMock(side_effect=apply)), \
         patch("app.api.nodes.ClientService.restore_committed_configs", new=AsyncMock()):
        response = await request("DELETE", "/api/v1/nodes/1", auth_headers)
    assert response.status_code == (204 if apply_ok else 502)
    async with TestingSessionLocal() as db:
        assert (await db.get(Node, 1) is None) == apply_ok
        client = await db.get(Client, cid)
        assert client.exit_node_id == (None if apply_ok else 1)
        assert client.sub_token == "same-subscription"
        assert (await db.get(NodeInvite, 1)).node_id == (None if apply_ok else 1)
        assert (await db.get(Node, 2)).priority == (0 if apply_ok else 1)
        assert (await db.get(Setting, "active_node_id")).value == ("2" if apply_ok else "1")
        assert (await db.get(Setting, "warp.node_id")).value == ("2" if apply_ok else "1")
        assert (await db.execute(text("PRAGMA foreign_key_check"))).all() == []


@pytest.mark.asyncio
async def test_delete_active_node_respects_keep_route_policy(auth_headers):
    cid = await seed()
    async with TestingSessionLocal() as db:
        (await db.get(Client, cid)).exit_node_id = 1
        (await db.get(Node, 2)).is_enabled = False
        db.add(Setting(key="failover.fallback_action", value="keep"))
        await db.commit()
    with patch("app.api.nodes.ClientService.sync_xray_clients", new=AsyncMock()) as sync:
        response = await request("DELETE", "/api/v1/nodes/1", auth_headers)
        assert response.status_code == 409
        sync.assert_not_awaited()
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, cid)).exit_node_id == 1
        assert await db.get(Node, 1) is not None


@pytest.mark.asyncio
async def test_failed_apply_restores_database_and_committed_runtime(auth_headers):
    cid = await seed()
    with patch("app.api.clients._sync_protocols", new=AsyncMock(side_effect=RuntimeError("apply failed"))), \
         patch("app.services.client_service.ClientService.restore_committed_configs", new=AsyncMock()) as restore:
        response = await request("PUT", f"/api/v1/clients/{cid}/exit-route", auth_headers, json={"node_id": 2})
    assert response.status_code == 502
    restore.assert_awaited_once()
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, cid)).exit_node_id is None


@pytest.mark.asyncio
async def test_route_validation_and_subadmin_cannot_assign(auth_headers):
    from app.api.auth import create_access_token
    cid = await seed()
    path = f"/api/v1/clients/{cid}/exit-route"
    for body in [{}, {"node_id": 0}, {"node_id": -1}, {"node_id": True}, {"node_id": "2"}, {"node_id": 2, "extra": True}]:
        assert (await request("PUT", path, auth_headers, json=body)).status_code == 422
    assert (await request("PUT", path, auth_headers, json={"node_id": 99})).status_code == 404
    assert (await request("PUT", path, auth_headers, json={"node_id": 3})).status_code == 422
    response = await request("POST", "/api/v1/users", auth_headers,
        json={"username": "issuer", "password": "ExampleIssuerPassword!", "can_issue_subscriptions": True})
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "issuer"})}
    assert (await request("GET", path, headers)).status_code == 403
    assert (await request("GET", "/api/v1/clients/exit-options", headers)).status_code == 403
    assert (await request("PUT", path, headers, json={"node_id": 2})).status_code == 403
    assert (await request("POST", "/api/v1/clients", headers, json={"name": "Тест", "exit_node_id": 2})).status_code == 403


@pytest.mark.asyncio
async def test_new_client_can_be_pinned_at_creation(auth_headers):
    await seed()
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        response = await request("POST", "/api/v1/clients", auth_headers, json={"name": "Андрей", "exit_node_id": 2})
    assert response.status_code == 201, response.text
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, response.json()["client"]["id"])).exit_node_id == 2


def entities(node_id=2):
    client = SimpleNamespace(id=7, exit_node_id=node_id, is_active=True, expires_at=None,
        monthly_traffic_limit=0, monthly_traffic_up=0, monthly_traffic_down=0,
        created_at=datetime.now(timezone.utc), traffic_period_start=None)
    node = SimpleNamespace(id=2, host="192.0.2.20", port=443, protocol="trojan", secret="example-node",
        is_enabled=True, public_key=None, short_id=None)
    def profile(kind, **kwargs):
        return SimpleNamespace(kind=kind, uuid="example-identity", auth="example-auth", ip_address=None, is_enabled=True, **kwargs)
    return client, node, profile


@pytest.mark.parametrize("kind", ["vless_reality_tcp", "vless_xhttp_reality", "vless_xhttp_tls", "hysteria2"])
def test_each_authenticated_protocol_keeps_fixed_exit_before_global_warp(kind):
    client, node, profile = entities()
    common = [{"type": "field", "domain": ["domain:blocked.example"], "outboundTag": "block"},
              {"type": "field", "domain": ["domain:example.com"], "outboundTag": "warp"}]
    rules = build_client_exit_rules([(profile(kind), client)], {kind}, {2: node}, common)
    assert rules[0]["outboundTag"] == "block" and rules[0]["domain"] == ["domain:blocked.example"]
    assert rules[1]["outboundTag"] == "node-2" and f"c7-{kind}@md-next" in rules[1]["user"]
    if kind == "vless_xhttp_tls":
        assert "c7-cdn@md-next" in rules[1]["user"]
    assert not any(rule["outboundTag"] == "warp" for rule in rules)


def test_awg_uses_peer_source_and_disabled_node_blocks_without_fallback():
    client, node, profile = entities()
    awg = profile("awg")
    awg.ip_address = "10.8.0.7/32"
    rules = build_client_exit_rules([(awg, client)], {"awg"}, {2: node}, [])
    assert rules == [{"type": "field", "inboundTag": ["awg-in"], "source": ["10.8.0.7"], "outboundTag": "node-2"}]
    node.is_enabled = False
    assert build_client_exit_rules([(awg, client)], {"awg"}, {2: node}, [])[0]["outboundTag"] == "block"
    assert build_client_exit_rules([(awg, client)], {"awg"}, {}, [])[0]["outboundTag"] == "block"


def test_default_paused_disabled_protocols_and_mtproto_do_not_get_pin_rules():
    client, node, profile = entities(None)
    rows = [(profile("vless_reality_tcp"), client)]
    assert build_client_exit_rules(rows, {"vless_reality_tcp"}, {2: node}, []) == []
    client.exit_node_id = 2
    client.is_active = False
    assert build_client_exit_rules(rows, {"vless_reality_tcp"}, {2: node}, []) == []
    client.is_active = True
    assert build_client_exit_rules(rows, set(), {2: node}, []) == []
    assert build_client_exit_rules([(profile("mtproto"), client)], {"mtproto"}, {2: node}, []) == []


def test_cdn_only_access_and_unhealthy_node_resume_without_route_change():
    client, node, profile = entities()
    tls = profile("vless_xhttp_tls")
    tls.is_enabled = False
    node.status = "unhealthy"
    assert build_client_exit_rules([(tls, client)], {tls.kind}, {2: node}, []) == [
        {"type": "field", "user": ["c7-cdn@md-next"], "outboundTag": "node-2"}]


@pytest.mark.asyncio
async def test_service_composes_adblock_pins_and_common_rules_in_order(auth_headers, monkeypatch):
    import json
    from app.models.routing import RoutingRule
    from app.services.client_service import ClientService
    from app.services.xray import XrayService
    cid = await seed()
    monkeypatch.setattr("app.services.client_service.apply_reality_sni", AsyncMock())
    adblock = {"type": "field", "domain": ["full:ads.example"], "user": [f"c{cid}-vless_reality_tcp@md-next"], "outboundTag": "block"}
    monkeypatch.setattr("app.services.client_service.build_adblock_rules", AsyncMock(side_effect=lambda *args: [adblock]))
    apply = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(XrayService, "apply_config", apply)
    async with TestingSessionLocal() as db:
        (await db.get(Client, cid)).exit_node_id = 2
        db.add_all([Setting(key="warp.usage", value="all"), Setting(key="warp.node_id", value="1"),
                    RoutingRule(domain_or_ip="full:example.com", action="direct"),
                    RoutingRule(domain_or_ip="full:blocked.example", action="block")])
        await db.flush()
        assert (await ClientService.sync_xray_clients(db))[0]
        options = apply.call_args.kwargs["profile_options"]
        rules = options["routing_rules"]
        assert rules[0] == adblock
        assert rules[1]["outboundTag"] == "block" and "user" in rules[1]
        assert rules[2]["outboundTag"] == "node-2"
        assert rules[3]["outboundTag"] == "direct"
        config = json.loads(XrayService.generate_config(**apply.call_args.kwargs))
        assert config["outbounds"][0]["tag"] == "warp"
        assert config["routing"]["rules"].index(rules[2]) < len(config["routing"]["rules"]) - 1
        assert config["routing"]["rules"][-1]["outboundTag"] == "warp"
        # Выключение ноды не сбрасывает назначение, а закрывает выход только её клиентов.
        (await db.get(Node, 2)).is_enabled = False
        assert (await ClientService.sync_xray_clients(db))[0]
        options = apply.call_args.kwargs["profile_options"]
        assert options["routing_rules"][2]["outboundTag"] == "block"
        assert (await db.get(Client, cid)).exit_node_id == 2


def test_two_clients_get_distinct_nodes_without_affecting_default_client():
    first, node, profile = entities(1)
    second, _, _ = entities(2)
    second.id = 8
    common, _, _ = entities(None)
    common.id = 9
    rows = [(profile("hysteria2"), client) for client in (first, second, common)]
    first_node = SimpleNamespace(**dict(vars(node), id=1))
    rules = build_client_exit_rules(rows, {"hysteria2"}, {1: first_node, 2: node}, [])
    assert rules == [{"type": "field", "user": ["c7-hysteria2@md-next"], "outboundTag": "node-1"},
                     {"type": "field", "user": ["c8-hysteria2@md-next"], "outboundTag": "node-2"}]
