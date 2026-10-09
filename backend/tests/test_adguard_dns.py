import base64
import json
from urllib.parse import unquote
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.models.setting import Setting
from app.models.client import Client
from app.services.adguard_dns import validate_query
from fastapi import HTTPException
from conftest import TestingSessionLocal
from test_client_access import seed

WIRE = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x07example\x03com\x00\x00\x01\x00\x01"
REPLY = WIRE[:2] + b"\x81\x80" + WIRE[4:]


def test_dns_query_bounds():
    assert validate_query(WIRE) == WIRE
    for wire in (b"", b"x" * 4097, WIRE[:2] + b"\x81\x00" + WIRE[4:], WIRE[:4] + b"\x00\x02" + WIRE[6:]):
        with pytest.raises(HTTPException):
            validate_query(wire)


@pytest.mark.asyncio
async def test_doh_requires_active_subscription_and_uses_personal_toggle(monkeypatch):
    cid = await seed()
    mock = AsyncMock(return_value=REPLY)
    monkeypatch.setattr("app.services.adguard_dns.resolve_query", mock)
    async with TestingSessionLocal() as db:
        db.add(Setting(key="adguard.enabled", value="true"))
        db.add(Setting(key=f"client.adblock.{cid}", value="true"))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        path = "/api/v1/sub/access-test/dns-query"
        response = await http.post(path, content=WIRE, headers={"Content-Type":"application/dns-message"})
        assert response.status_code == 200 and response.content == REPLY
        mock.assert_awaited_with(cid, True, WIRE)
        async with TestingSessionLocal() as db:
            (await db.get(Setting,f"client.adblock.{cid}")).value = "false"
            await db.commit()
        response = await http.get(path, params={"dns":base64.urlsafe_b64encode(WIRE).decode().rstrip("=")})
        assert response.status_code == 200
        mock.assert_awaited_with(cid, False, WIRE)
        assert (await http.post(path,content=WIRE)).status_code == 415
        assert (await http.get(path,params={"dns":"!"})).status_code == 400
        assert (await http.get("/api/v1/sub/unknown/dns-query")).status_code == 404
        async with TestingSessionLocal() as db:
            (await db.get(Client,cid)).is_active = False
            await db.commit()
        assert (await http.get(path)).status_code == 403


@pytest.mark.asyncio
async def test_happ_adguard_overrides_both_dns_only_when_selected(monkeypatch):
    cid = await seed()
    monkeypatch.setenv("PANEL_PUBLIC_URL","https://panel.example.com")
    async with TestingSessionLocal() as db:
        db.add_all([Setting(key="adguard.enabled",value="true"),
                    Setting(key="adguard.bootstrap_ip",value="192.0.2.1"),
                    Setting(key=f"client.adblock.{cid}",value="true")])
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as http:
        response = await http.get("/api/v1/sub/access-test?format=raw")
        profile = json.loads(base64.b64decode(unquote(response.headers["routing"].rsplit("/",1)[-1])))
        assert profile["RemoteDNSDomain"] == profile["DomesticDNSDomain"] == "https://panel.example.com/api/v1/sub/access-test/dns-query"
        assert profile["DnsHosts"]["panel.example.com"] == "192.0.2.1"
        async with TestingSessionLocal() as db:
            (await db.get(Setting,f"client.adblock.{cid}")).value = "false"
            await db.commit()
        response = await http.get("/api/v1/sub/access-test?format=raw")
        profile = json.loads(base64.b64decode(unquote(response.headers["routing"].rsplit("/",1)[-1])))
        assert profile["RemoteDNSDomain"] == "https://cloudflare-dns.com/dns-query"


@pytest.mark.asyncio
async def test_bridge_selects_fixed_filtered_and_unfiltered_upstreams(monkeypatch):
    import httpx
    from app.services.adguard_dns import resolve_query
    seen = []
    class FakeHTTP:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            seen.append(url)
            return httpx.Response(200, content=REPLY, request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx,"AsyncClient",FakeHTTP)
    assert await resolve_query(999,True,WIRE) == REPLY
    assert await resolve_query(999,False,WIRE) == REPLY
    assert seen == ["http://127.0.0.1:3001/dns-query/c-999","https://cloudflare-dns.com/dns-query"]
