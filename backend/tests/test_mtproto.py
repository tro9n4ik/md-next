import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services import mtproto
from app.services.crypto import decrypt_secret, encrypt_secret
from app.services.profiles import create_profiles, make_profile_data
from app.services.xray import XrayService
from app.main import app
from conftest import TestingSessionLocal

spec = importlib.util.spec_from_file_location("mtproto_runtime", Path(__file__).resolve().parents[2] / "scripts/mtproto-runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def config(users=None):
    return {"port": 8445, "domain": "example.com", "users": {"p1": "a" * 32} if users is None else users}


@pytest.mark.parametrize("port", [True, 80, 443, 2053, 8000, 8444, 10085, 10909, 10910, 30001, 40002, 70000])
def test_reserved_ports_are_rejected(port):
    with pytest.raises(ValueError):
        runtime.validate({**config(), "port": port})


@pytest.mark.parametrize("domain", ['example.com\nproxies=[]', '127.0.0.1', 'localhost', '../etc/passwd', 'https://example.com', 'a"b.com'])
def test_domain_cannot_inject_configuration(domain):
    with pytest.raises(ValueError):
        runtime.validate({**config(), "domain": domain})


def test_config_has_one_node_proxy_and_no_direct_fallback():
    text = runtime.render(config())
    assert 'proxies = ["socks5://127.0.0.1:10909"]' in text
    assert 'api-bind-to = "127.0.0.1:10910"' in text
    assert 'p1 = "ee' + "a" * 32 + '6578616d706c652e636f6d"' in text
    assert runtime.validate(config({})) == config({})
    for users in [{"../root": "a" * 32}, {"p1": "not-a-key"}, {"p1": "a" * 32, "p2": "a" * 32}]:
        with pytest.raises(ValueError):
            runtime.validate(config(users))


def test_unconfirmed_reload_stops_service_and_restores_config(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.os, "chown", lambda *args: None)
    target = tmp_path / "config.toml"
    before = runtime.render(config()).encode()
    target.write_bytes(before)
    monkeypatch.setattr(runtime, "CONFIG", target)
    monkeypatch.setattr(runtime, "prepare", lambda: SimpleNamespace(pw_gid=0))
    monkeypatch.setattr(runtime, "install_binary", lambda: False)
    with patch.object(runtime, "command") as command, patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run, \
         patch.object(runtime, "verify_applied", side_effect=RuntimeError("no acknowledgement")):
        with pytest.raises(RuntimeError):
            runtime.apply(config({"p1": "b" * 32}))
    assert target.read_bytes() == before
    assert any(call.args[1] == "reload" for call in command.call_args_list)
    assert any(call.args[0][1:3] == ["disable", "--now"] for call in run.call_args_list)


def test_empty_access_set_stops_proxy_without_download_or_dummy_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.os, "chown", lambda *args: None)
    target = tmp_path / "config.toml"
    target.write_text("secret-data")
    monkeypatch.setattr(runtime, "CONFIG", target)
    with patch.object(runtime, "install_binary") as download, patch.object(runtime, "prepare") as prepare, \
         patch.object(runtime, "command"):
        runtime.apply(config({}))
    download.assert_not_called()
    prepare.assert_not_called()
    assert "secret-data" not in target.read_text()


def test_download_digest_mismatch_never_replaces_binary(tmp_path, monkeypatch):
    import io
    binary = tmp_path / "mtg"
    binary.write_bytes(b"previous binary")
    monkeypatch.setattr(runtime, "BINARY", binary)
    monkeypatch.setattr(runtime, "CONFIG", tmp_path / "config.toml")
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    with patch.object(runtime.urllib.request, "urlopen", return_value=io.BytesIO(b"untrusted archive")):
        with pytest.raises(ValueError, match="checksum"):
            runtime.install_binary()
    assert binary.read_bytes() == b"previous binary"


def test_mtproto_inbound_is_local_and_cannot_fall_back_to_master_or_warp():
    for node in [None, SimpleNamespace(id=7, is_enabled=True, secret="a", protocol="trojan", host="192.0.2.2", port=443)]:
        data = json.loads(XrayService.generate_config([], server_private_key="private", server_name="example.com", active_node=node,
                          profile_options={"enabled": {"mtproto"}, "warp_usage": "off"}))
        inbound = next(i for i in data["inbounds"] if i.get("tag") == "mtproto-in")
        assert inbound["listen"] == "127.0.0.1" and inbound["settings"]["udp"] is False
        rule = next(r for r in data["routing"]["rules"] if r.get("inboundTag") == ["mtproto-in"])
        assert rule["outboundTag"] == ("node-7" if node else "block")


@pytest.mark.asyncio
async def test_profiles_have_distinct_encrypted_secrets_and_telegram_links():
    async with TestingSessionLocal() as db:
        db.add(Setting(key="profiles.enabled.mtproto", value="true"))
        db.add(Setting(key="profiles.enabled.awg", value="false"))
        clients = [Client(name=name, phone="", email="") for name in ["One", "Two"]]
        db.add_all(clients)
        await db.flush()
        profiles = [next(p for p in await create_profiles(db, c) if p.kind == "mtproto") for c in clients]
        keys = [decrypt_secret(p.private_key_enc) for p in profiles]
        assert keys[0] != keys[1] and all(len(k) == 32 for k in keys)
        assert all(p.auth is None and p.private_key_enc != key for p, key in zip(profiles, keys))
        link = make_profile_data(clients[0], profiles[0], {"protocol.reality.server_address": "vpn.example.com", "mtproto.domain": "example.com"})
        params = parse_qs(urlsplit(link).query)
        assert params["secret"] == ["ee" + keys[0] + "example.com".encode().hex()]
        assert params["server"] == ["vpn.example.com"] and params["port"] == ["8445"]
        assert not any(p.kind == "mtproto" for p in await create_profiles(db, clients[0]))


@pytest.mark.asyncio
async def test_sync_excludes_paused_expired_quota_and_disabled_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("MTPROTO_STAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SERVER_HOST", "example.com")
    async with TestingSessionLocal() as db:
        db.add(Setting(key="profiles.enabled.mtproto", value="true"))
        for index in range(5):
            client = Client(name=str(index), phone="", email="", is_active=index != 1)
            if index == 2:
                client.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
            if index == 3:
                client.monthly_traffic_limit = 100
                client.monthly_traffic_up = 100
            db.add(client)
            await db.flush()
            if index == 3:
                from app.services.client_limits import traffic_period
                client.traffic_period_start = traffic_period(client)[0]
            db.add(ClientProfile(client_id=client.id, kind="mtproto", is_enabled=index != 4,
                                 private_key_enc=encrypt_secret(f"{index + 1:032x}")))
        await db.flush()
        with patch("app.services.privileges.enabled", return_value=True), patch("app.services.privileges.call", AsyncMock(return_value=(0, "", ""))) as call:
            assert (await mtproto.sync_config(db))[0]
        data = json.loads((tmp_path / "mtproto.json").read_text())
        assert list(data["users"].values()) == [f"{1:032x}"]
        assert (tmp_path / "mtproto.json").stat().st_mode & 0o077 == 0
        call.assert_awaited_once_with("mtproto-apply", timeout=120)


@pytest.mark.asyncio
async def test_collector_cursor_survives_restart_and_rollback():
    payload = {"started_at": "start-a", "users": {"p1": {"bytes_in": 100, "bytes_out": 50}}}
    response = httpx.Response(200, json=payload, request=httpx.Request("GET", "http://127.0.0.1/stats"))
    client = AsyncMock()
    client.__aenter__.return_value.get.return_value = response
    async with TestingSessionLocal() as db:
        with patch.object(mtproto.httpx, "AsyncClient", return_value=client):
            assert await mtproto.collect_deltas(db) == {1: (100, 50)}
            await db.commit()
            assert await mtproto.collect_deltas(db) == {1: (0, 0)}
            await db.rollback()
            response._content = json.dumps({**payload, "users": {"p1": {"bytes_in": 125, "bytes_out": 90}}}).encode()
            assert await mtproto.collect_deltas(db) == {1: (25, 40)}
            await db.rollback()
            assert await mtproto.collect_deltas(db) == {1: (25, 40)}
            await db.commit()
            response._content = json.dumps({"started_at": "start-b", "users": {"p1": {"bytes_in": 10, "bytes_out": 5}}}).encode()
            assert await mtproto.collect_deltas(db) == {1: (10, 5)}


@pytest.mark.asyncio
async def test_mtproto_is_separate_from_raw_subscription_but_present_on_personal_page():
    async with TestingSessionLocal() as db:
        db.add(Setting(key="profiles.enabled.mtproto", value="true"))
        db.add(Setting(key="mtproto.domain", value="example.com"))
        client = Client(name="Telegram", phone="", email="", sub_token="mtproto-test-token")
        db.add(client)
        await db.flush()
        db.add(ClientProfile(client_id=client.id, kind="mtproto", private_key_enc=encrypt_secret("a" * 32), is_enabled=True))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        raw = await api.get("/api/v1/sub/mtproto-test-token?format=raw")
        assert raw.status_code == 200 and b"tg://" not in base64.b64decode(raw.content)
        page = await api.get("/api/v1/sub/mtproto-test-token?format=page")
        assert page.status_code == 200 and 'tg://proxy?' in page.text and 'Подключить Telegram' in page.text
        async with TestingSessionLocal() as db:
            from sqlalchemy import select
            user = (await db.execute(select(Client).where(Client.sub_token == "mtproto-test-token"))).scalar_one()
            user.is_active = False
            await db.commit()
        page = await api.get("/api/v1/sub/mtproto-test-token?format=page")
        assert 'tg://proxy?' not in page.text


@pytest.mark.asyncio
async def test_existing_client_mutations_reach_mtproto_sync(tmp_path, monkeypatch):
    from app.services.client_service import ClientService
    monkeypatch.setenv("MTPROTO_STAGE_DIR", str(tmp_path))
    async with TestingSessionLocal() as db:
        with patch("app.services.xray.XrayService.apply_config", AsyncMock(return_value=(True, "ok"))), \
             patch("app.services.client_service.apply_reality_sni", AsyncMock()), \
             patch.object(mtproto, "sync_config", AsyncMock(return_value=(True, "ok"))) as sync:
            assert (await ClientService.sync_xray_clients(db))[0]
            sync.assert_awaited_once_with(db)
            sync.reset_mock()
            assert (await ClientService.sync_xray_clients(db, active_node=None))[0]
            sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_can_enable_issue_rotate_pause_and_delete_individual_access(auth_headers, tmp_path, monkeypatch):
    from app.models.node import Node
    monkeypatch.setenv("MTPROTO_STAGE_DIR", str(tmp_path))
    monkeypatch.setenv("SERVER_HOST", "example.com")
    async with TestingSessionLocal() as db:
        db.add(Setting(key="profiles.enabled.awg", value="false"))
        node = Node(name="Exit", host="192.0.2.10", port=443, protocol="trojan", secret="node-secret", is_enabled=True)
        db.add(node)
        clients = [Client(name=name, phone="", email="") for name in ["First", "Second"]]
        db.add_all(clients)
        await db.flush()
        db.add(Setting(key="active_node_id", value=str(node.id)))
        ids = [client.id for client in clients]
        await db.commit()
    with patch("app.services.xray.XrayService.apply_config", AsyncMock(return_value=(True, "ok"))), \
         patch("app.services.client_service.apply_reality_sni", AsyncMock()), \
         patch("app.services.awg.AWGService.sync_server_config", AsyncMock(return_value=(True, "ok"))), \
         patch("app.services.privileges.enabled", return_value=True), \
         patch("app.services.privileges.call", AsyncMock(return_value=(0, "", ""))):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=auth_headers) as api:
            response = await api.put("/api/v1/settings/protocols", json={"enabled": {"mtproto": True}, "mtproto": {"domain": "example.com"}})
            assert response.status_code == 200, response.text
            initial = json.loads((tmp_path / "mtproto.json").read_text())["users"]
            assert len(initial) == 2
            profiles = (await api.get(f"/api/v1/clients/{ids[0]}/profiles")).json()["profiles"]
            profile = next(p for p in profiles if p["kind"] == "mtproto")
            assert profile["data"].startswith("tg://proxy?") and profile["key_available"]
            name = f"p{profile['id']}"
            response = await api.post(f"/api/v1/clients/{ids[0]}/profiles/{profile['id']}/regenerate")
            assert response.status_code == 200, response.text
            rotated = json.loads((tmp_path / "mtproto.json").read_text())["users"]
            assert rotated[name] != initial[name]
            assert {k: v for k, v in rotated.items() if k != name} == {k: v for k, v in initial.items() if k != name}
            response = await api.put(f"/api/v1/clients/{ids[0]}", json={"is_active": False})
            assert response.status_code == 200, response.text
            paused = json.loads((tmp_path / "mtproto.json").read_text())["users"]
            assert name not in paused and len(paused) == 1
            response = await api.delete(f"/api/v1/clients/{ids[1]}")
            assert response.status_code == 204, response.text
            assert json.loads((tmp_path / "mtproto.json").read_text())["users"] == {}
