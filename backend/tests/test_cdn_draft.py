from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.services.cdn import make_cdn_link, validate_domain


def test_cdn_link_uses_tls_without_vision_and_keeps_existing_identity():
    profile = SimpleNamespace(kind="vless_xhttp_tls", uuid="test-uuid")
    client = SimpleNamespace(name="Клиент")
    settings = {"cdn.domain": "cdn.example.com", "profiles.path.vless_xhttp_tls": "/test-path"}
    assert make_cdn_link(client, profile, settings) == ""
    link = urlsplit(make_cdn_link(client, profile, settings, preview=True))
    params = parse_qs(link.query)
    assert link.username == "test-uuid" and link.hostname == "cdn.example.com"
    assert params["mode"] == ["packet-up"] and params["security"] == ["tls"]
    assert params["path"] == ["/test-path/cdn-get"] and "flow" not in params
    import json
    extra = json.loads(params["extra"][0])
    assert extra["uplinkHTTPMethod"] == "GET" and extra["uplinkDataPlacement"] == "header"
    assert extra["scMaxEachPostBytes"] == 2048 and params["alpn"] == ["h2"]


@pytest.mark.parametrize("domain", ["https://a.example", "localhost", "127.0.0.1", "a..example", "a.example/path", "-a.example"])
def test_bad_cdn_domain_rejected(domain):
    with pytest.raises(ValueError):
        validate_domain(domain)


@pytest.mark.asyncio
async def test_draft_does_not_allow_unverified_activation(auth_headers, monkeypatch):
    async def failed(*args):
        return {"ok": False, "message": "POST: 413"}
    monkeypatch.setattr("app.api.cdn.probe_cdn", failed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/cdn")).status_code in {401, 403}
        saved = await client.put("/api/v1/cdn", headers=auth_headers, json={"domain": "cdn.example.com"})
        assert saved.status_code == 200 and saved.json()["enabled"] is False
        rejected = await client.put("/api/v1/cdn", headers=auth_headers, json={"domain": "other.example.com", "enabled": True})
        assert rejected.status_code == 409
        assert (await client.get("/api/v1/cdn", headers=auth_headers)).json()["domain"] == "cdn.example.com"


@pytest.mark.asyncio
async def test_enable_disable_and_protocol_guard(auth_headers, monkeypatch):
    from app.models.setting import Setting
    from conftest import TestingSessionLocal
    async def passed(*args):
        return {"ok": True, "message": "POST: 200"}
    monkeypatch.setattr("app.api.cdn.probe_cdn", passed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/cdn/check", json={})).status_code in {401, 403}
        payload = {"domain": "cdn.example.com", "enabled": True}
        assert (await client.put("/api/v1/cdn", headers=auth_headers, json=payload)).status_code == 409
        async with TestingSessionLocal() as db:
            db.add(Setting(key="profiles.enabled.vless_xhttp_tls", value="true"))
            await db.commit()
        response = await client.put("/api/v1/cdn", headers=auth_headers, json=payload)
        assert response.status_code == 200 and response.json()["enabled"] is True
        settings = {"cdn.enabled": "true", "cdn.domain": "cdn.example.com"}
        assert make_cdn_link(SimpleNamespace(name="Клиент"), SimpleNamespace(kind="vless_xhttp_tls", uuid="id"), settings)
        response = await client.put("/api/v1/cdn", headers=auth_headers, json={**payload, "enabled": False})
        assert response.status_code == 200 and response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_probe_rejects_private_dns_without_request(monkeypatch):
    from app.services.cdn import probe_cdn
    monkeypatch.setattr("app.services.cdn.socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("127.0.0.1", 443))])
    assert not (await probe_cdn("cdn.example.com", "/xhttp"))["ok"]


def test_cdn_separate_inbound_keeps_tls_identity_and_settings():
    import json
    from app.services.xray import XrayService
    identity = [{"id": "existing-uuid", "email": "profile-1"}]
    config = json.loads(XrayService.generate_config([], "private", server_name="example.com", profile_options={
        "enabled": {"vless_xhttp_tls"}, "xhttp_tls_path": "/original", "xhttp_tls_mode": "auto",
        "vless_xhttp_tls_clients": identity,
    }))
    cdn = next(i for i in config["inbounds"] if i.get("tag") == "cdn-get")
    normal = next(i for i in config["inbounds"] if i.get("port") == 8446)
    assert cdn["listen"] == "127.0.0.1" and cdn["port"] == 8447
    assert cdn["settings"]["clients"] == normal["settings"]["clients"] == identity
    assert normal["streamSettings"]["xhttpSettings"] == {"path": "/original", "mode": "auto"}
    assert cdn["streamSettings"]["xhttpSettings"]["path"] == "/original/cdn-get"
