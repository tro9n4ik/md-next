import base64
import json
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import AsyncMock
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.main import app
from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.adblock import build_adblock_rules, enabled_for_client
from app.services.happ_routing import build_happ_routing_link
from app.services.xray import XrayService
from conftest import TestingSessionLocal
from test_client_access import seed

def decode_routing(link):
    return json.loads(base64.b64decode(unquote(link.rsplit("/", 1)[-1])))

def test_happ_adblock_is_opt_in_and_can_be_removed():
    assert decode_routing(build_happ_routing_link({}))["BlockSites"] == []
    profile = decode_routing(build_happ_routing_link({}, adblock_enabled=True))
    assert profile["BlockSites"] == ["geosite:category-ads-all"]
    assert profile["RouteOrder"] == "block-proxy-direct"
    assert "geosite:category-ru" in profile["DirectSites"]
    assert "geoip:ru" in profile["DirectIp"]
    assert decode_routing(build_happ_routing_link({}, adblock_enabled=False))["BlockSites"] == []

@pytest.mark.asyncio
async def test_rules_are_scoped_to_selected_client_and_precede_routes(monkeypatch):
    monkeypatch.setattr("app.services.adblock.validate_rule_value", lambda value: "domain")
    async with TestingSessionLocal() as db:
        db.add(Setting(key="client.adblock.1", value="true"))
        await db.flush()
        clients = [SimpleNamespace(id=i, is_active=True, access_blocked=False, expires_at=None,
                                  monthly_traffic_limit=0, traffic_limit=0, created_at=None, traffic_period_start=None) for i in (1,2)]
        profiles=[]
        for c in clients:
            for kind in ("vless_reality_tcp", "vless_xhttp_tls", "hysteria2", "awg"):
                profiles.append((SimpleNamespace(kind=kind, is_enabled=True, uuid="uuid",
                                                ip_address=f"10.8.0.{c.id}/32"), c))
        rules=await build_adblock_rules(db, profiles, {"vless_reality_tcp","vless_xhttp_tls","hysteria2","awg"})
        assert len(rules)==2
        assert rules[0]["user"]==["c1-cdn@md-next","c1-hysteria2@md-next","c1-vless_reality_tcp@md-next","c1-vless_xhttp_tls@md-next"]
        assert rules[1]["source"]==["10.8.0.1"] and rules[1]["inboundTag"]==["awg-in"]
        route={"type":"field","domain":["domain:example.com"],"outboundTag":"direct"}
        config=json.loads(XrayService.generate_config([], "private", profile_options={"routing_rules":rules+[route],"enabled":set()}))
        emitted=config["routing"]["rules"]
        assert emitted.index(rules[0]) < emitted.index(route)
        assert await build_adblock_rules(db, profiles, set()) == []

@pytest.mark.asyncio
async def test_access_toggle_isolated_preserves_keys_and_subscription(auth_headers, monkeypatch):
    cid=await seed()
    monkeypatch.setattr("app.api.clients.validate_rule_value", lambda value: "domain")
    monkeypatch.setattr("app.api.clients._sync_protocols", AsyncMock())
    async with TestingSessionLocal() as db:
        other=Client(name="Другой",phone="",email="",sub_token="other-adblock")
        db.add(other); await db.commit(); other_id=other.id
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as http:
        assert not (await http.get(f"/api/v1/clients/{cid}/profiles",headers=auth_headers)).json()["adblock_enabled"]
        for enabled in (True,False):
            response=await http.put(f"/api/v1/clients/{cid}/access",headers=auth_headers,json={"adblock_enabled":enabled})
            assert response.status_code==200, response.text
            assert response.json()["adblock_enabled"]==enabled
            assert not (await http.get(f"/api/v1/clients/{other_id}/profiles",headers=auth_headers)).json()["adblock_enabled"]
            raw=await http.get("/api/v1/sub/access-test")
            routing=raw.headers["routing"]
            assert bool(decode_routing(routing)["BlockSites"])==enabled
    async with TestingSessionLocal() as db:
        assert (await db.get(Client,cid)).sub_token=="access-test"
        p=(await db.execute(select(ClientProfile).where(ClientProfile.client_id==cid))).scalar_one()
        assert p.uuid=="existing-id" and p.is_enabled

@pytest.mark.asyncio
async def test_failed_apply_rolls_back_and_missing_list_is_rejected(auth_headers,monkeypatch):
    cid=await seed()
    restored=AsyncMock()
    monkeypatch.setattr("app.services.client_service.ClientService.restore_committed_configs",restored)
    monkeypatch.setattr("app.api.clients.validate_rule_value",lambda value:"domain")
    monkeypatch.setattr("app.api.clients._sync_protocols",AsyncMock(side_effect=RuntimeError("failure")))
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as http:
        response=await http.put(f"/api/v1/clients/{cid}/access",headers=auth_headers,json={"adblock_enabled":True})
        assert response.status_code==502
        assert not (await http.get(f"/api/v1/clients/{cid}/profiles",headers=auth_headers)).json()["adblock_enabled"]
        def missing(value): raise ValueError("Не найден geosite.dat")
        monkeypatch.setattr("app.api.clients.validate_rule_value",missing)
        response=await http.put(f"/api/v1/clients/{cid}/access",headers=auth_headers,json={"adblock_enabled":True})
        assert response.status_code==409
        assert (await http.put(f"/api/v1/clients/{cid}/access",json={"adblock_enabled":True})).status_code in (401,403)
    restored.assert_awaited_once()
