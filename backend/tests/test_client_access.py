import base64
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.profiles import PROFILE_KINDS
from app.services.xray import XrayService
from conftest import TestingSessionLocal


async def seed():
    async with TestingSessionLocal() as db:
        client = Client(name="Проверка", phone="", email="", sub_token="access-test")
        db.add(client)
        await db.flush()
        db.add(ClientProfile(client_id=client.id, kind="vless_xhttp_tls", uuid="existing-id"))
        for kind in PROFILE_KINDS:
            db.add(Setting(key=f"profiles.enabled.{kind}", value="true" if kind == "vless_xhttp_tls" else "false"))
        db.add_all([Setting(key="cdn.enabled", value="true"), Setting(key="cdn.domain", value="cdn.example.com")])
        await db.commit()
        return client.id


@pytest.mark.asyncio
async def test_pause_resume_preserves_subscription_and_activity(auth_headers, monkeypatch):
    from app.services import client_activity as activity
    cid = await seed()
    monkeypatch.setattr(activity, '_sources', {})
    monkeypatch.setattr(activity, '_seen', {})
    monkeypatch.setattr('app.api.clients._sync_protocols', AsyncMock())
    activity.source_checked('xray')
    activity.record_activity(cid, 'vless_xhttp_tls', 10, 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as http:
        result = await http.get('/api/v1/clients', headers=auth_headers)
        assert result.json()[0]['connected_protocols'] == ['vless_xhttp_tls']
        for enabled in (False, True):
            result = await http.put(f'/api/v1/clients/{cid}', headers=auth_headers, json={'is_active': enabled})
            assert result.status_code == 200, result.text
            clients = (await http.get('/api/v1/clients', headers=auth_headers)).json()
            assert clients[0]['connection_status'] == ('online' if enabled else 'offline')
            async with TestingSessionLocal() as db:
                assert (await db.get(Client, cid)).sub_token == 'access-test'
                profiles = (await db.execute(select(ClientProfile))).scalars().all()
                assert len(profiles) == 1 and profiles[0].uuid == 'existing-id' and profiles[0].is_enabled


@pytest.mark.asyncio
async def test_independent_cdn_access_preserves_identity(auth_headers, monkeypatch):
    cid = await seed()
    monkeypatch.setattr("app.api.clients._sync_protocols", AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for tls, cdn in [(False, True), (True, False), (False, False)]:
            response = await http.put(f"/api/v1/clients/{cid}/access", headers=auth_headers,
                                      json={"profiles": {"vless_xhttp_tls": tls, "cdn": cdn}})
            assert response.status_code == 200, response.text
            raw = await http.get("/api/v1/sub/access-test")
            links = base64.b64decode(raw.text).decode().splitlines()
            assert len(links) == int(tls) + int(cdn)
            assert any("cdn.example.com" in link for link in links) == cdn
            assert all("existing-id@" in link for link in links)
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, cid)).sub_token == "access-test"


@pytest.mark.asyncio
async def test_failed_sync_rolls_back_access(auth_headers, monkeypatch):
    cid = await seed()
    monkeypatch.setattr("app.api.clients._sync_protocols", AsyncMock(side_effect=RuntimeError("failure")))
    restored = AsyncMock()
    monkeypatch.setattr("app.services.client_service.ClientService.restore_committed_configs", restored)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.put(f"/api/v1/clients/{cid}/access", headers=auth_headers,
                                  json={"profiles": {"vless_xhttp_tls": False, "cdn": False}})
        assert response.status_code == 502
        raw = await http.get("/api/v1/sub/access-test")
        assert len(base64.b64decode(raw.text).decode().splitlines()) == 2
        assert (await http.put(f"/api/v1/clients/{cid}/access", headers=auth_headers,
                              json={"profiles": {"unknown": True}})).status_code == 422
        assert (await http.put(f"/api/v1/clients/{cid}/access", headers=auth_headers,
                              json={"profiles": {"awg": True}})).status_code == 409
        assert (await http.put(f"/api/v1/clients/{cid}/access", json={"profiles": {}})).status_code in (401, 403)
    restored.assert_awaited_once()


def test_xray_separates_cdn_and_normal_tls_clients():
    config = json.loads(XrayService.generate_config([], "private", profile_options={
        "enabled": {"vless_xhttp_tls"}, "vless_xhttp_tls_clients": [{"id": "tls-only"}],
        "cdn_clients": [{"id": "cdn-only"}],
    }))
    normal = next(item for item in config["inbounds"] if item.get("port") == 8446)
    cdn = next(item for item in config["inbounds"] if item.get("tag") == "cdn-get")
    assert normal["settings"]["clients"] == [{"id": "tls-only"}]
    assert cdn["settings"]["clients"] == [{"id": "cdn-only"}]


@pytest.mark.asyncio
async def test_service_enforces_rights_and_expiration(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.services.client_service import ClientService
    cid = await seed()
    async with TestingSessionLocal() as db:
        profile = (await db.execute(select(ClientProfile))).scalar_one()
        profile.is_enabled = False
        db.add(Setting(key=f"client.cdn.{cid}", value="true"))
        expired = Client(name="Истёкший", phone="", email="", expires_at=datetime.now(timezone.utc)-timedelta(days=1))
        db.add(expired)
        await db.flush()
        db.add(ClientProfile(client_id=expired.id, kind="vless_xhttp_tls", uuid="expired-id"))
        await db.commit()
        apply = AsyncMock(return_value=(True, ""))
        monkeypatch.setattr("app.services.xray.XrayService.apply_config", apply)
        assert (await ClientService.sync_xray_clients(db, active_node=None))[0]
        options = apply.call_args.kwargs["profile_options"]
        assert options["vless_xhttp_tls_clients"] == []
        assert [item["id"] for item in options["cdn_clients"]] == ["existing-id"]


@pytest.mark.asyncio
async def test_subscription_page_and_raw_are_isolated_and_escaped(monkeypatch):
    cid = await seed()
    monkeypatch.setenv("PANEL_PUBLIC_URL", "https://panel.example.com")
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        client.name = '<script>alert("test")</script>'
        client.email = 'private@example.com'
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        page = await http.get("/api/v1/sub/access-test", headers={"Accept": "text/html", "Sec-Fetch-Mode": "navigate"})
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        assert '&lt;script&gt;' in page.text and '<script>alert(' not in page.text
        assert 'private@example.com' not in page.text and 'existing-id' not in page.text
        assert '?format=raw' in page.text and 'data:image/png;base64,' in page.text
        assert page.headers['cache-control'] == 'no-store'
        import hashlib,re
        scripts=re.findall(r'<script>(.*?)</script>',page.text,re.DOTALL)
        assert len(scripts)==1
        digest=base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
        csp=page.headers['content-security-policy']
        assert f"script-src 'sha256-{digest}'" in csp
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert "img-src data:" in csp and "'unsafe-inline'" not in csp.split('script-src')[1]
        raw = await http.get("/api/v1/sub/access-test?format=raw", headers={"Accept": "text/html"})
        assert 'text/plain' in raw.headers['content-type']
        assert len(base64.b64decode(raw.text).decode().splitlines()) == 2
        assert (await http.get("/api/v1/sub/other-token", headers={"Accept": "text/html"})).status_code == 404
        assert (await http.get("/api/v1/sub/access-test?format=invalid")).status_code == 422


@pytest.mark.asyncio
async def test_subscription_format_requires_browser_navigation():
    await seed()
    browser='Mozilla/5.0 AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as http:
        cases=[({'Accept':'text/html'},False),
               ({'Accept':'text/html','User-Agent':browser},True),
               ({'Accept':'text/html','User-Agent':browser,'Sec-Fetch-Mode':'cors'},False),
               ({'Accept':'text/html;q=0','Sec-Fetch-Mode':'navigate'},False),
               ({'Accept':'text/html','Sec-Fetch-Mode':'navigate'},True)]
        # Compatibility examples, not captures from real devices/apps.
        cases += [({'Accept':'text/html,*/*','User-Agent':name},False)
                  for name in ['Happ','v2rayNG','Hiddify','Streisand','Shadowrocket']]
        for headers,html in cases:
            response=await http.get('/api/v1/sub/access-test',headers=headers)
            assert response.status_code==200
            assert ('text/html' in response.headers['content-type'])==html
            assert 'Sec-Fetch-Mode' in response.headers['vary']
        forced=await http.get('/api/v1/sub/access-test?format=page',headers={'User-Agent':'Happ'})
        assert 'text/html' in forced.headers['content-type']
