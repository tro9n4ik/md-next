from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from app.db.database import AsyncSessionLocal
from app.models.client import Client, ClientProfile
from app.services.client_service import ClientService
from app.services.profiles import make_profile_data


SETTINGS = {
    "protocol.reality.server_address": "vpn.example.com",
    "protocol.reality.server_name": "vpn.example.com",
    "protocol.reality.target": "127.0.0.1:8080",
    "protocol.reality.private_key": "test-private-key",
    "protocol.reality.public_key": "test-public-key",
    "protocol.reality.fingerprint": "firefox",
    "protocol.reality.short_id": "1234",
    "protocol.reality.flow": "xtls-rprx-vision",
    "profiles.port.vless_xhttp_reality": "2053",
    "profiles.path.vless_xhttp_reality": "/test",
    "profiles.path.vless_xhttp_tls": "/tls",
    "protocol.xhttp.reality_mode": "auto",
    "protocol.xhttp.tls_mode": "auto",
    "profiles.port.hysteria2": "443",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["xtls-rprx-vision", ""])
async def test_subscription_and_server_keep_vision_exclusive_to_tcp(flow):
    settings = {**SETTINGS, "protocol.reality.flow": flow}
    apply = AsyncMock(return_value=(True, "ok"))
    kinds = {"vless_reality_tcp", "vless_xhttp_reality", "vless_xhttp_tls"}
    async with AsyncSessionLocal() as db:
        client = Client(name="Test", phone="", email="", is_active=True)
        db.add(client)
        await db.flush()
        profiles = {
            kind: ClientProfile(client_id=client.id, kind=kind,
                                uuid=f"00000000-0000-0000-0000-{index:012d}", is_enabled=True)
            for index, kind in enumerate(sorted(kinds), 1)
        }
        db.add_all(profiles.values())
        await db.commit()
        with patch("app.services.client_service.get_profile_settings", AsyncMock(return_value=settings)), \
             patch("app.services.client_service.enabled_profile_kinds", AsyncMock(return_value=kinds)), \
             patch("app.services.client_service.apply_reality_sni", AsyncMock()), \
             patch("app.services.client_service.XrayService.get_active_node", AsyncMock(return_value=None)), \
             patch("app.services.client_service.XrayService.apply_config", apply), \
             patch("app.services.client_service.log_event"):
            ok, _ = await ClientService.sync_xray_clients(db)
        assert ok
        options = apply.await_args.kwargs["profile_options"]
        assert "flow" not in options["vless_xhttp_reality_clients"][0]
        assert "flow" not in options["vless_xhttp_tls_clients"][0]
        assert apply.await_args.kwargs["clients"][0].get("flow", "") == flow
        for kind, profile in profiles.items():
            uri = urlsplit(make_profile_data(client, profile, settings))
            query = parse_qs(uri.query)
            assert uri.username == profile.uuid
            assert query.get("flow", [""])[0] == (flow if kind == "vless_reality_tcp" else "")
            if kind != "vless_xhttp_tls":
                assert query["fp"] == ["firefox"]
                assert query["pbk"] == ["test-public-key"]
                assert query["sid"] == ["1234"]
