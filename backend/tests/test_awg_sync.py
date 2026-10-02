import os
import base64
from unittest import mock

import pytest

from app.services.awg import AWGService


def test_awg31_client_config_matches_server_parameters():
    server_private_key = base64.b64encode(bytes(range(32))).decode("ascii")
    parameters = AWGService.protocol_parameters(server_private_key)
    client_config = AWGService.generate_client_conf(
        private_key="client-private",
        address="10.0.0.2/32",
        server_public_key="server-public",
        server_endpoint="example.com:51820",
        server_private_key=server_private_key,
    )

    for key, value in parameters.items():
        assert f"{key} = {value}" in client_config
    assert parameters["HeaderProtectionKey"] != AWGService.protocol_parameters(
        base64.b64encode(bytes(reversed(range(32)))).decode("ascii")
    )["HeaderProtectionKey"]


@pytest.mark.asyncio
async def test_awg_sync_starts_interface_on_first_apply(tmp_path):
    db = mock.AsyncMock()
    db.get.return_value = None
    result = mock.Mock()
    result.all.return_value = []
    db.execute.return_value = result
    commands = []

    async def fake_run_cmd(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("awg", "show", "awg0"):
            return 1, "", "Интерфейс не найден"
        return 0, "", ""

    with mock.patch.object(AWGService, "get_server_settings", new=mock.AsyncMock(return_value={
        "server_private_key": "private-test-key", "server_ip": "10.0.0.1", "port": 51820,
    })), mock.patch("app.services.awg.run_cmd", side_effect=fake_run_cmd):
        success, message = await AWGService.sync_server_config(db, str(tmp_path / "awg0.conf"))

    assert success is True, message
    assert ("awg-quick", "up", "awg0") in commands


@pytest.mark.asyncio
async def test_awg_sync_strips_config_before_syncconf(tmp_path):
    db = mock.AsyncMock()
    db.get.return_value = None
    result = mock.Mock()
    result.all.return_value = []
    db.execute.return_value = result
    commands = []

    async def fake_run_cmd(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("awg", "show", "awg0"):
            return 0, "public-key", ""
        if args[:3] == ("awg-quick", "strip", "awg0"):
            return 0, "[Interface]\nPrivateKey = private-test-key\n", ""
        return 0, "", ""

    with mock.patch.object(AWGService, "get_server_settings", new=mock.AsyncMock(return_value={
        "server_private_key": "private-test-key", "server_ip": "10.0.0.1", "port": 51820,
    })), mock.patch("app.services.awg.run_cmd", side_effect=fake_run_cmd):
        success, message = await AWGService.sync_server_config(db, str(tmp_path / "awg0.conf"))

    assert success is True, message
    assert any(args[:3] == ("awg-quick", "strip", "awg0") for args in commands)
    sync_call = next(args for args in commands if args[:2] == ("awg", "syncconf"))
    stripped_path = sync_call[-1]
    assert stripped_path != str(tmp_path / "awg0.conf")
    assert not os.path.exists(stripped_path)
