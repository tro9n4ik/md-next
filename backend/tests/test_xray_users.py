import copy
import json
from unittest.mock import AsyncMock
import pytest
from app.services.xray_users import user_changes, apply_user_changes


def config():
    return {"api": {"services": ["StatsService", "HandlerService"]}, "inbounds": [
        {"tag": "api-in", "listen": "127.0.0.1", "port": 10085, "protocol": "dokodemo-door"},
        {"tag": "vless-in", "port": 443, "protocol": "vless", "settings": {"clients": [
            {"id": "00000000-0000-0000-0000-000000000001", "email": "c1-vless_reality_tcp@md-next"}]}}], "outbounds": []}


def test_only_user_changes_eligible():
    old = config(); new = copy.deepcopy(old)
    new["inbounds"][1]["settings"]["clients"] = []
    assert len(user_changes(old, new)) == 1
    assert old == config()
    new["outbounds"].append({"protocol": "freedom"})
    assert user_changes(old, new) is None


@pytest.mark.parametrize("mutation", ["api", "tag", "email", "port"])
def test_unsafe_or_structural_change_requires_full_config(mutation):
    old = config(); new = copy.deepcopy(old)
    new["inbounds"][1]["settings"]["clients"][0]["id"] = "replacement"
    if mutation == "api": old["api"]["services"] = ["StatsService"]
    if mutation == "tag": new["inbounds"][1]["tag"] = "different"
    if mutation == "email": new["inbounds"][1]["settings"]["clients"][0]["email"] = "--option"
    if mutation == "port": new["inbounds"][1]["port"] = 444
    assert user_changes(old, new) is None


@pytest.mark.asyncio
async def test_cli_zero_without_confirmation_is_failure(monkeypatch):
    old = config(); new = copy.deepcopy(old)
    new["inbounds"][1]["settings"]["clients"] = []
    command = AsyncMock(return_value=(0, "Removed 0 user(s) in total.", ""))
    monkeypatch.setattr("app.services.xray_users.run_cmd", command)
    with pytest.raises(RuntimeError): await apply_user_changes(old, new)


@pytest.mark.asyncio
async def test_failed_add_restores_removed_user(monkeypatch):
    old = config(); new = copy.deepcopy(old)
    new["inbounds"][1]["settings"]["clients"][0]["id"] = "replacement"
    calls = []
    async def command(*args, **kwargs):
        calls.append(args)
        if args[2] == "rmu": return 0, "Removed 1 user(s) in total.", ""
        with open(args[-1], encoding="utf8") as stream:
            payload = json.load(stream)
        assert "streamSettings" not in payload["inbounds"][0]
        assert payload["inbounds"][0]["port"] == 443
        user = payload["inbounds"][0]["settings"]["clients"][0]
        return 0, f"Added {0 if user['id'] == 'replacement' else 1} user(s) in total.", ""
    monkeypatch.setattr("app.services.xray_users.run_cmd", command)
    with pytest.raises(RuntimeError): await apply_user_changes(old, new)
    assert len(calls) == 3
