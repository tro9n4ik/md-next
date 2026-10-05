import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.services.warp import WarpService
from app.services.xray import XrayService
from app.services.routing_rules import to_xray_rule, validate_rule_value
from app.api.routing import create_routing_rule
from app.schemas.routing import RoutingRuleCreate
from fastapi import HTTPException


@pytest.mark.parametrize("status,registration,expected", [
    ("Status update: Connected\nMode: Warp", "Account type: Unlimited\nDevice ID: abc", ("Connected", "warp", True)),
    ("Status: Disconnected\nMode: Proxy\nProxy: 127.0.0.1:41000", "Account type: Free", ("Disconnected", "proxy", True)),
    ("Connection status: Connecting\nService mode: Warp+doh", "Not registered", ("Connecting", "warp", False)),
])
def test_parse_status_formats(status, registration, expected):
    parsed = WarpService.parse_status(status, registration)
    assert (parsed["state"], parsed["mode"], parsed["registered"]) == expected


@pytest.mark.asyncio
async def test_status_when_warp_cli_missing():
    db = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result
    with patch.object(WarpService, "cli_path", return_value=None):
        result = await WarpService.status(db)
    assert result["installed"] is False
    assert "cloudflare-warp" in result["instruction"]


@pytest.mark.asyncio
async def test_trace_test_uses_socks_proxy_and_parses_response():
    response = httpx.Response(200, text="ip=203.0.113.4\nloc=RU\nwarp=plus\n", request=httpx.Request("GET", "https://www.cloudflare.com/cdn-cgi/trace"))
    client = AsyncMock()
    client.__aenter__.return_value.get.return_value = response
    with patch("app.services.warp.httpx.AsyncClient", return_value=client) as http_client:
        result = await WarpService.test_proxy(41234)
    assert http_client.call_args.kwargs["proxy"] == "socks5://127.0.0.1:41234"
    assert result == {"ip": "203.0.113.4", "country": "RU", "warp": "plus"}


def test_xray_config_has_warp_outbound_and_warp_routing_rule():
    config = json.loads(XrayService.generate_config(
        clients=[], server_private_key="private", server_name="example.com",
        profile_options={"enabled": {"vless_reality_tcp"}, "warp_usage": "rules", "warp_port": 40123,
                         "routing_rules": [{"type": "field", "domain": ["domain:example.org"], "outboundTag": "warp"}]},
    ))
    warp = next(item for item in config["outbounds"] if item["tag"] == "warp")
    assert warp["settings"]["servers"][0]["port"] == 40123
    assert config["routing"]["rules"][-1]["outboundTag"] == "warp"


def test_geosite_requires_asset_dat(monkeypatch, tmp_path):
    monkeypatch.setenv("XRAY_LOCATION_ASSET", str(tmp_path))
    monkeypatch.setattr('app.services.routing_rules.Path.is_file', lambda _: False)
    with pytest.raises(ValueError, match="geosite.dat"):
        validate_rule_value("geosite:category-ads")


@pytest.mark.asyncio
async def test_geosite_rule_without_asset_returns_http_400(monkeypatch, tmp_path):
    monkeypatch.setenv("XRAY_LOCATION_ASSET", str(tmp_path))
    monkeypatch.setattr('app.services.routing_rules.Path.is_file', lambda _: False)
    with pytest.raises(HTTPException) as error:
        await create_routing_rule(RoutingRuleCreate(domain_or_ip="geosite:category-ads", action="warp"), AsyncMock())
    assert error.value.status_code == 400
    assert "geosite.dat" in error.value.detail


def test_routing_rule_maps_actions():
    rule = SimpleNamespace(id=1, domain_or_ip="full:example.org", action="warp", target_node_id=None)
    assert to_xray_rule(rule, {}) == {"type": "field", "domain": ["full:example.org"], "outboundTag": "warp"}


@pytest.mark.asyncio
async def test_license_key_is_never_returned():
    key = "test-license-key-never-echoed"
    with patch.object(WarpService, "_run_process", new=AsyncMock(return_value=(0, key, ""))) as run:
        success, message = await WarpService.set_license(key)
    assert success
    assert key not in message
    assert run.await_args.args[-1] == key
