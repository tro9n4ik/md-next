"""Node-only WARP setup must never change local registration or settings."""
from unittest.mock import patch
import httpx
import pytest
from sqlalchemy import select
from app.models.setting import Setting
from app.services.warp import WarpService
from conftest import TestingSessionLocal


@pytest.mark.asyncio
@pytest.mark.parametrize("trace,success", [
    ({"ip":"203.0.113.1","country":"DE","warp":"on"}, True),
    ({"ip":"203.0.113.1","country":"NL","warp":"plus"}, True),
    ({"ip":"203.0.113.1","country":"RU","warp":"off"}, False),
])
async def test_setup_only_checks_active_node(trace, success):
    async with TestingSessionLocal() as db:
        with patch.object(WarpService, "target", return_value={"node_id":1,"name":"Node","port":40000,"expected_country":""}), patch.object(WarpService,"test_target",return_value=trace), patch.object(WarpService,"register") as register, patch.object(WarpService,"set_mode") as mode, patch.object(WarpService,"connect") as connect:
            result, _ = await WarpService.setup_warp_proxy(db)
        assert result is success
        register.assert_not_awaited()
        mode.assert_not_awaited()
        connect.assert_not_awaited()
        assert (await db.execute(select(Setting))).scalars().all() == []


@pytest.mark.asyncio
async def test_failed_node_check_preserves_database():
    async with TestingSessionLocal() as db:
        db.add(Setting(key="warp.usage",value="off")); await db.commit()
        with patch.object(WarpService,"target",return_value={"node_id":1,"name":"Node","port":40000,"expected_country":""}), patch.object(WarpService,"test_target",side_effect=httpx.ConnectError("unavailable")), patch.object(WarpService,"register") as register:
            success, _ = await WarpService.setup_warp_proxy(db)
        assert not success
        register.assert_not_awaited()
        assert (await db.get(Setting,"warp.usage")).value == "off"


@pytest.mark.asyncio
async def test_missing_node_never_uses_local_warp():
    async with TestingSessionLocal() as db:
        with patch.object(WarpService,"test_proxy") as local:
            success, _ = await WarpService.setup_warp_proxy(db)
        assert not success
        local.assert_not_awaited()


def test_settings_output_recognizes_warp_proxy():
    parsed = WarpService.parse_status("Status update: Connected\n(user set) Mode: WarpProxy\n(user set) Proxy port: 41000", "Account type: Free")
    assert parsed["mode"] == "proxy"
    assert parsed["port"] == 41000
    assert parsed["registered"]
