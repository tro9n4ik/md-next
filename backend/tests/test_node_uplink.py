import asyncio
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.node_transport import NODE_UPLINK_TAG, node_outbound, node_uplink_outbounds, node_uplink_warp_port
from app.services.watchdog import WatchdogService
from app.services.xray import XrayService


NODE = {"id": 1, "host": "192.0.2.1", "port": 24443, "protocol": "vless",
        "secret": "00000000-0000-0000-0000-000000000001", "public_key": "A" * 43}


@pytest.fixture(autouse=True)
def clear_uplink(monkeypatch):
    monkeypatch.delenv("NODE_UPLINK_WARP_PORT", raising=False)


@pytest.mark.parametrize("value", ["", "0"])
def test_uplink_is_opt_in(monkeypatch, value):
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", value)
    assert node_uplink_warp_port() is None
    assert node_uplink_outbounds() == []
    assert "sockopt" not in node_outbound(NODE)["streamSettings"]


@pytest.mark.parametrize("value", ["-1", "65536", "40000.5", "http://example.test", "１２３", "9" * 5000])
def test_invalid_uplink_port_does_not_generate_a_route(monkeypatch, value):
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", value)
    with pytest.raises(ValueError, match="NODE_UPLINK_WARP_PORT"):
        node_outbound(NODE)


@pytest.mark.parametrize("protocol", ["vless", "trojan"])
def test_uplink_keeps_node_transport_and_authentication(monkeypatch, protocol):
    row = {**NODE, "protocol": protocol}
    original = node_outbound(row)
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", "40000")
    through_uplink = node_outbound(row)
    assert through_uplink["streamSettings"].pop("sockopt") == {"dialerProxy": NODE_UPLINK_TAG}
    assert through_uplink == original
    relay = node_uplink_outbounds()
    assert relay == [{"tag": NODE_UPLINK_TAG, "protocol": "socks", "settings": {
        "servers": [{"address": "127.0.0.1", "port": 40000}]}}]


def generate(active_node, options):
    return json.loads(XrayService.generate_config([], active_node=active_node,
        server_private_key="synthetic", server_name="example.test", profile_options=options))


def test_config_rebuild_keeps_uplink_for_active_and_backup_nodes(monkeypatch):
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", "40000")
    active = SimpleNamespace(**NODE, is_enabled=True)
    options = {"nodes": [NODE, {**NODE, "id": 2}], "warp_usage": "rules", "warp_node_id": 1, "warp_port": 40001}
    for _ in range(2):
        config = generate(active, options)
        outbounds = {o["tag"]: o for o in config["outbounds"]}
        assert config["outbounds"][0]["tag"] == "node-1"
        assert len([o for o in config["outbounds"] if o["tag"] == NODE_UPLINK_TAG]) == 1
        for tag in ("node-1", "node-2"):
            assert outbounds[tag]["streamSettings"]["sockopt"]["dialerProxy"] == NODE_UPLINK_TAG
        assert outbounds["warp"]["streamSettings"]["sockopt"]["dialerProxy"] == "node-1"
        assert "streamSettings" not in outbounds[NODE_UPLINK_TAG]


def test_direct_exit_is_preserved_when_uplink_is_enabled(monkeypatch):
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", "40000")
    config = generate(None, {"nodes": [NODE], "warp_usage": "off"})
    assert config["outbounds"][0]["tag"] == "direct"
    direct = config["outbounds"][0]
    assert direct["protocol"] == "freedom"
    assert "streamSettings" not in direct
    assert all(o["tag"] != "warp" for o in config["outbounds"])
    no_nodes = generate(None, {})
    assert all(o["tag"] != NODE_UPLINK_TAG for o in no_nodes["outbounds"])


@pytest.mark.asyncio
@pytest.mark.parametrize("control", [False, True])
async def test_temporary_node_checks_use_the_same_uplink(monkeypatch, control):
    from app.services import warp_node
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", "40000")
    monkeypatch.setattr(warp_node, "find_command", lambda _: "xray")

    async def capture(*args, **kwargs):
        config = json.loads(Path(args[-1]).read_text())
        outbounds = {o["tag"]: o for o in config["outbounds"]}
        assert outbounds["node-1"]["streamSettings"]["sockopt"]["dialerProxy"] == NODE_UPLINK_TAG
        assert NODE_UPLINK_TAG in outbounds
        assert config["outbounds"][0]["tag"] == ("node-1" if control else "warp")
        raise RuntimeError("Проверка остановлена до запуска процесса")

    monkeypatch.setattr(warp_node.asyncio, "create_subprocess_exec", capture)
    with pytest.raises(RuntimeError, match="до запуска"):
        await warp_node.test_node_proxy(SimpleNamespace(**NODE, is_enabled=True), 40001, None, control=control)


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize("host", ["192.0.2.9", "node.example.test"])
async def test_watchdog_checks_actual_socks_path_without_direct_fallback(monkeypatch, success, host):
    seen = []
    closed = asyncio.Event()

    async def socks_server(reader, writer):
        try:
            greeting = await reader.readexactly(2)
            assert greeting[0] == 5
            await reader.readexactly(greeting[1])
            writer.write(b"\x05\x00")
            await writer.drain()
            header = await reader.readexactly(4)
            assert header[:3] == b"\x05\x01\x00"
            if header[3] == 1:
                address = ".".join(str(v) for v in await reader.readexactly(4))
            elif header[3] == 3:
                length = (await reader.readexactly(1))[0]
                address = (await reader.readexactly(length)).decode()
            else:
                raise AssertionError("Неожиданный формат адреса")
            destination_port = struct.unpack("!H", await reader.readexactly(2))[0]
            seen.append((address, destination_port))
            writer.write(b"\x05" + (b"\x00" if success else b"\x05") + b"\x00\x01\x7f\x00\x00\x01\x00\x00")
            await writer.drain()
            if success:
                assert await reader.read(1) == b""
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_server(socks_server, "127.0.0.1", 0)
    try:
        monkeypatch.setenv("NODE_UPLINK_WARP_PORT", str(server.sockets[0].getsockname()[1]))
        direct_connection = AsyncMock(wraps=asyncio.open_connection)
        with patch("app.services.watchdog.asyncio.open_connection", direct_connection):
            ok, latency = await WatchdogService()._check_node_ping(host, 24443)
        assert ok is success
        assert latency >= 0
        assert seen == [(host, 24443)]
        assert all(call.args != (host, 24443) for call in direct_connection.call_args_list)
        await asyncio.wait_for(closed.wait(), 1)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_watchdog_rejects_invalid_uplink_configuration(monkeypatch):
    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", "invalid")
    with pytest.raises(ValueError, match="NODE_UPLINK_WARP_PORT"):
        await WatchdogService()._check_node_ping("192.0.2.1", 443)


@pytest.mark.asyncio
async def test_invalid_uplink_does_not_change_node_health_or_route(monkeypatch):
    from app.db.database import AsyncSessionLocal
    from app.models.node import Node
    from app.models.setting import Setting

    async with AsyncSessionLocal() as session:
        node = Node(name="Test", host="192.0.2.1", port=443, protocol="trojan", secret="synthetic",
                    is_enabled=True, is_active=True, status="healthy")
        session.add(node)
        await session.flush()
        node_id = node.id
        session.add(Setting(key="active_node_id", value=str(node_id)))
        await session.commit()

    monkeypatch.setenv("NODE_UPLINK_WARP_PORT", "invalid")
    watchdog = WatchdogService()
    with patch.object(watchdog, "_switch", new_callable=AsyncMock) as switch:
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="NODE_UPLINK_WARP_PORT"):
                await watchdog._check_cycle(session)
            await session.rollback()
        switch.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        node = await session.get(Node, node_id)
        assert node.is_active is True
        assert node.status == "healthy"
        assert (await session.get(Setting, "active_node_id")).value == str(node_id)
