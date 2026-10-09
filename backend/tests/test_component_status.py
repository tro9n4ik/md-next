import pytest

from app.services import component_status as inventory
from app.services.shell import CommandTimeout


@pytest.mark.parametrize(("output", "expected"), [
    ("Xray 26.3.27 (go1.26.1)", "26.3.27"),
    ("nginx version: nginx/1.28.3 (Ubuntu)", "1.28.3"),
    ("AdGuard Home, version v0.107.79", "0.107.79"),
    ("amneziawg-tools v3.1.20260812", "3.1.20260812"),
    ("warp-cli 2026.7.1377.0", "2026.7.1377.0"),
    ("unrecognised output", None),
])
def test_version(output, expected):
    assert inventory._version(output) == expected


@pytest.mark.asyncio
async def test_service_status_and_stderr_version(monkeypatch):
    calls = []
    monkeypatch.setattr(inventory, "find_command", lambda _: "/usr/bin/nginx")
    async def run(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "nginx":
            return 0, "", "nginx version: nginx/1.28.3"
        return 3, "inactive\n", ""
    monkeypatch.setattr(inventory, "run_cmd", run)
    row = await inventory._probe(inventory.COMPONENTS[2])
    assert row["version"] == "1.28.3"
    assert row["status"] == "stopped"
    assert calls[0][0] == ("nginx", "-v")
    assert calls[1][0] == ("systemctl", "is-active", "nginx")
    assert all(call[1]["timeout"] == 3 for call in calls)


@pytest.mark.asyncio
async def test_missing_does_not_execute(monkeypatch):
    monkeypatch.setattr(inventory, "find_command", lambda _: None)
    async def unexpected(*args, **kwargs):
        raise AssertionError("Missing commands must not execute")
    monkeypatch.setattr(inventory, "run_cmd", unexpected)
    row = await inventory._probe(inventory.COMPONENTS[1])
    assert row["status"] == "not_installed"
    assert row["version"] is None


@pytest.mark.asyncio
async def test_timeout_keeps_other_components(monkeypatch):
    monkeypatch.setattr(inventory, "find_command", lambda _: "/installed")
    async def run(*args, **kwargs):
        if args[0] == "xray":
            raise CommandTimeout("timeout")
        if args[0] == "systemctl":
            return 0, "active", ""
        return 0, "version v1.2.3", ""
    monkeypatch.setattr(inventory, "run_cmd", run)
    result = await inventory.get_components()
    rows = {row["key"]: row for row in result["components"]}
    assert rows["xray"]["status"] == "unknown"
    assert rows["adguard"]["status"] == "running"
    assert rows["python"]["version"]
    assert result["scope"] == "panel_server"
