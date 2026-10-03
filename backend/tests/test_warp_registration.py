"""Регистрация через ноду без циклического выхода через WARP и без чужих правил."""

from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from app.models.node import Node
from app.models.setting import Setting
from app.services.warp import WarpService
from app.services.warp_registration import COMMENT, cleanup, close_servers, node_config
from conftest import TestingSessionLocal


def test_node_proxy_contains_only_selected_outbound():
    node = {"tag": "node-1", "protocol": "trojan", "settings": {"servers": [{"address": "example.com", "password": "тест"}]}}
    config = node_config({"outbounds": [{"tag": "warp", "protocol": "socks"}, node], "routing": {"rules": [{"outboundTag": "warp"}]}}, 1, 10999)
    assert config["outbounds"] == [node]
    assert "routing" not in config
    assert config["inbounds"][0]["listen"] == "127.0.0.1"
    with pytest.raises(RuntimeError):
        node_config({"outbounds": [dict(node, proxySettings={"tag": "warp"})]}, 1, 10999)


def test_cleanup_removes_only_own_rules():
    saved = f'-A OUTPUT -p tcp --dport 443 -m comment --comment {COMMENT} -j REDIRECT --to-ports 33333\n-A OUTPUT -m comment --comment another-service -j ACCEPT\n'
    with patch("app.services.warp_registration.shutil.which", return_value="iptables"), \
         patch("app.services.warp_registration.subprocess.run", return_value=SimpleNamespace(stdout=saved)) as run:
        cleanup()
    deletes = [call.args[0] for call in run.call_args_list if "-D" in call.args[0]]
    assert len(deletes) == 2
    assert all(COMMENT in args and "another-service" not in args for args in deletes)


@pytest.mark.asyncio
async def test_register_uses_selected_node_and_preserves_existing_registration():
    async with TestingSessionLocal() as db:
        db.add_all([Node(id=1, name="Первая", host="one.test", port=443, protocol="trojan", secret="тест", priority=0),
                    Node(id=2, name="Выбранная", host="two.test", port=443, protocol="trojan", secret="тест", priority=10),
                    Setting(key="active_node_id", value="2")])
        await db.commit()
        with patch.object(WarpService, "_run_process", return_value=(1, "", "Missing registration")), \
             patch.object(WarpService, "_register_via_node", return_value=(True, "Готово")) as register:
            assert (await WarpService.register(db))[0]
            register.assert_awaited_once_with(2)
        with patch.object(WarpService, "_run_process", return_value=(0, "Account type: Free", "")), \
             patch.object(WarpService, "_register_via_node", new=AsyncMock()) as register:
            assert (await WarpService.register(db))[0]
            register.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_cancels_active_connections_before_waiting_for_server():
    import asyncio
    connections = set()
    accepted = asyncio.Event()

    async def relay(reader, writer):
        task = asyncio.current_task()
        connections.add(task)
        accepted.set()
        try:
            await reader.read()
        finally:
            writer.close()
            connections.discard(task)

    server = await asyncio.start_server(relay, "127.0.0.1", 0)
    _, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    try:
        await accepted.wait()
        await asyncio.wait_for(close_servers((server,), connections), 1)
        assert not connections
    finally:
        writer.close()
        await writer.wait_closed()
