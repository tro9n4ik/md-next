import base64
import json
from urllib.parse import unquote
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting
from app.models.client import Client, ClientProfile
from app.services.happ_routing import build_happ_routing_link
from app.services.nginx import apply_reality_sni, apply_xhttp_tls_path
from app.services.xray import XrayService, reality_target_xver


def test_doh_bootstrap_uses_configured_ips_for_both_resolvers():
    link = build_happ_routing_link(
        {
            "dns.remote_domain": "https://remote.example/dns-query",
            "dns.remote_ip": "9.9.9.9",
            "dns.domestic_type": "DoH",
            "dns.domestic_domain": "https://local.example/query",
            "dns.domestic_ip": "8.8.8.8",
        }
    )
    profile = json.loads(
        base64.b64decode(unquote(link.removeprefix("happ://routing/onadd/")))
    )
    assert profile["DnsHosts"] == {
        "remote.example": "9.9.9.9",
        "local.example": "8.8.8.8",
    }


@pytest.mark.parametrize(
    "dest,xver",
    [
        ("127.0.0.1:8080", 1),
        ("localhost:8080", 1),
        ("example.org:443", 0),
        ("127.0.0.1:9443", 0),
    ],
)
def test_reality_proxy_header_depends_on_target(dest, xver):
    assert reality_target_xver(dest) == xver
    config = json.loads(
        XrayService.generate_config(
            [],
            dest=dest,
            profile_options={
                "enabled": {"vless_reality_tcp", "vless_xhttp_reality"},
            },
        )
    )
    for inbound in config["inbounds"]:
        reality = inbound.get("streamSettings", {}).get("realitySettings")
        if reality:
            assert reality["xver"] == xver


STREAM = """map $ssl_preread_server_name $backend_name {
    old.example xray_backend;
    panel.example panel_backend;
    default fake_backend;
}
upstream xray_backend { server 127.0.0.1:8444; }
"""


@pytest.mark.asyncio
async def test_sni_update_keeps_existing_routes_and_is_idempotent(
    tmp_path, monkeypatch
):
    target = tmp_path / "stream.conf"
    target.write_text(STREAM)
    monkeypatch.setenv("NGINX_STREAM_CONFIG", str(target))
    run = AsyncMock(return_value=(0, "", ""))
    with patch("app.services.nginx.run_cmd", run):
        await apply_reality_sni("new.example")
        await apply_reality_sni("new.example")
    assert "old.example xray_backend;" in target.read_text()
    assert "panel.example panel_backend;" in target.read_text()
    assert target.read_text().count("new.example xray_backend;") == 1
    assert run.await_count == 2


@pytest.mark.asyncio
async def test_reality_cannot_take_over_panel_domain(tmp_path, monkeypatch):
    target = tmp_path / "stream.conf"
    target.write_text(STREAM)
    monkeypatch.setenv("NGINX_STREAM_CONFIG", str(target))
    with pytest.raises(ValueError, match="занят"):
        await apply_reality_sni("panel.example")
    assert target.read_text() == STREAM


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["validation", "reload", "timeout"])
async def test_sni_failure_restores_original_file(tmp_path, monkeypatch, failure):
    target = tmp_path / "stream.conf"
    target.write_text(STREAM)
    monkeypatch.setenv("NGINX_STREAM_CONFIG", str(target))
    responses = {
        "validation": [(1, "", "invalid"), (0, "", "")],
        "reload": [(0, "", ""), (1, "", "reload failed"), (0, "", "")],
        "timeout": [RuntimeError("timeout"), (0, "", "")],
    }
    with patch("app.services.nginx.run_cmd", AsyncMock(side_effect=responses[failure])):
        with pytest.raises(RuntimeError):
            await apply_reality_sni("new.example")
    assert target.read_text() == STREAM


@pytest.mark.asyncio
async def test_xhttp_reload_failure_restores_previous_path(tmp_path, monkeypatch):
    target = tmp_path / "site.conf"
    original = "server {\n listen 127.0.0.1:8080 ssl;\n location / { root /tmp; }\n}\n"
    target.write_text(original)
    monkeypatch.setenv("NGINX_PANEL_CONFIG", str(target))
    with patch(
        "app.services.nginx.run_cmd",
        AsyncMock(side_effect=[(0, "", ""), (1, "", "reload failed"), (0, "", "")]),
    ):
        with pytest.raises(RuntimeError):
            await apply_xhttp_tls_path("/secret-path")
    assert target.read_text() == original


@pytest.mark.asyncio
async def test_unchanged_xray_config_keeps_existing_connections(tmp_path):
    target = tmp_path / "xray.json"
    config = json.loads(XrayService.generate_config([]))
    target.write_text(json.dumps(config))  # formatting differences are irrelevant
    run = AsyncMock(return_value=(0, "active", ""))
    with patch("app.services.xray.run_cmd", run):
        ok, _ = await XrayService.apply_config([], config_path=str(target))
    assert ok
    assert run.await_args_list[0].args == ("systemctl", "is-active", "xray")
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_xray_restart_exception_restores_and_starts_previous_config(tmp_path):
    target = tmp_path / "xray.json"
    target.write_text("previous")
    run = AsyncMock(
        side_effect=[(0, "", ""), RuntimeError("restart timeout"), (0, "", "")]
    )
    with patch("app.services.xray.run_cmd", run):
        ok, reason = await XrayService.apply_config([], config_path=str(target))
    assert not ok
    assert "timeout" in reason
    assert target.read_text() == "previous"
    assert run.await_args_list[-1].args == ("systemctl", "restart", "xray")


@pytest.mark.asyncio
async def test_reregister_selected_node_updates_trojan_password(auth_headers):
    async with AsyncSessionLocal() as db:
        node = Node(
            name="exit",
            host="203.0.113.77",
            port=443,
            protocol="trojan",
            secret="old-secret",
            is_enabled=True,
        )
        db.add(node)
        await db.flush()
        db.add(Setting(key="active_node_id", value=str(node.id)))
        await db.commit()
    apply = AsyncMock(return_value=(True, "ok"))
    with patch("app.services.xray.XrayService.apply_config", apply):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            invite = await client.post(
                "/api/v1/nodes/invites", headers=auth_headers, json={"name": "exit"}
            )
            response = await client.post(
                "/api/v1/nodes/register",
                json={
                    "token": invite.json()["token"],
                    "host": "203.0.113.77",
                    "port": 443,
                },
            )
    assert response.status_code == 201
    secret = response.json()["secret"]
    assert secret != "old-secret"
    assert apply.await_args.kwargs["active_node"].secret == secret
    assert apply.await_args.kwargs["profile_options"]["nodes"][0]["secret"] == secret


@pytest.mark.asyncio
async def test_toggle_node_failure_does_not_commit_partial_state(auth_headers):
    async with AsyncSessionLocal() as db:
        node = Node(
            name="exit",
            host="203.0.113.77",
            port=443,
            protocol="trojan",
            secret="secret",
            is_enabled=True,
        )
        db.add(node)
        await db.commit()
        node_id = node.id
    with patch(
        "app.services.xray.XrayService.apply_config",
        AsyncMock(return_value=(False, "invalid config")),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/v1/nodes/{node_id}/toggle", headers=auth_headers
            )
    assert response.status_code == 502
    async with AsyncSessionLocal() as db:
        assert (await db.get(Node, node_id)).is_enabled is True


@pytest.mark.asyncio
async def test_manual_direct_with_keep_is_still_explicitly_allowed():
    from app.services.client_service import ClientService

    async with AsyncSessionLocal() as db:
        db.add_all(
            [
                Setting(key="active_node_id", value="123"),
                Setting(key="failover.fallback_action", value="keep"),
            ]
        )
        await db.commit()
        apply = AsyncMock(return_value=(True, "ok"))
        with patch("app.services.xray.XrayService.apply_config", apply):
            assert (await ClientService.sync_xray_clients(db, active_node=None))[0]
        assert (
            apply.await_args.kwargs["profile_options"]["unavailable_selected_node"]
            is False
        )


@pytest.mark.asyncio
async def test_environment_keep_blocks_a_missing_selected_node(monkeypatch):
    from app.services.client_service import ClientService
    monkeypatch.setenv("FAILOVER_FALLBACK_ACTION", "keep")
    async with AsyncSessionLocal() as db:
        db.add(Setting(key="active_node_id", value="123"))
        await db.commit()
        apply = AsyncMock(return_value=(True, "ok"))
        with patch("app.services.xray.XrayService.apply_config", apply):
            assert (await ClientService.sync_xray_clients(db))[0]
        options = apply.await_args.kwargs["profile_options"]
        config = json.loads(XrayService.generate_config([], profile_options=options))
        assert config["outbounds"][0]["tag"] == "block"


@pytest.mark.asyncio
async def test_failed_profile_rotation_restores_credentials_in_xray(auth_headers):
    old_uuid = "00000000-0000-0000-0000-000000000999"
    async with AsyncSessionLocal() as db:
        client = Client(name="stable", phone="", email="", is_active=True)
        db.add(client)
        await db.flush()
        profile = ClientProfile(
            client_id=client.id,
            kind="vless_reality_tcp",
            uuid=old_uuid,
            is_enabled=True,
        )
        db.add(profile)
        await db.commit()
        client_id, profile_id = client.id, profile.id
    observed = []

    async def applied(**kwargs):
        observed.append([item["id"] for item in kwargs["clients"]])
        return True, "ok"

    with patch(
        "app.services.xray.XrayService.apply_config", AsyncMock(side_effect=applied)
    ), patch(
        "app.api.clients.AWGService.sync_server_config",
        AsyncMock(side_effect=[(False, "AWG unavailable"), (True, "ok")]),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/clients/{client_id}/profiles/{profile_id}/regenerate",
                headers=auth_headers,
            )
    assert response.status_code == 502
    assert observed[0] != [old_uuid]
    assert observed[-1] == [old_uuid]
    async with AsyncSessionLocal() as db:
        assert (await db.get(ClientProfile, profile_id)).uuid == old_uuid
