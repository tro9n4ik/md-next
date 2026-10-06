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
        if args[:3] == ("awg", "showconf", "awg0"):
            return 0, "[Interface]\nPrivateKey = previous-test-key\n", ""
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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["up", "strip", "sync", "snapshot"])
async def test_awg_failed_apply_restores_file_and_runtime(tmp_path, failure):
    path = tmp_path / "awg0.conf"
    original = b"[Interface]\nPrivateKey = old-file-key\n"
    path.write_bytes(original)
    runtime = "[Interface]\nPrivateKey = live-key\n[Peer]\nPublicKey = live-peer\nEndpoint = 192.0.2.1:1234\n"
    db = mock.AsyncMock()
    db.get.return_value = None
    db.execute.return_value.all = mock.Mock(return_value=[])
    sync_contents = []

    async def run(*args, **kwargs):
        if args[:2] == ("awg", "show"):
            return (1 if failure == "up" else 0), "", ""
        if args[:2] == ("awg", "showconf"):
            return (1, "", "SECRET") if failure == "snapshot" else (0, runtime, "")
        if args[:2] == ("awg-quick", "up"):
            return 1, "", "SECRET"
        if args[:2] == ("awg-quick", "strip"):
            return (1, "", "SECRET") if failure == "strip" else (0, "new-runtime", "")
        if args[:2] == ("awg", "syncconf"):
            from pathlib import Path
            sync_contents.append(Path(args[-1]).read_text())
            return (1, "", "SECRET") if len(sync_contents) == 1 else (0, "", "")
        raise AssertionError(args)

    with mock.patch.object(AWGService, "get_server_settings", return_value={
        "server_private_key": "new-key", "server_ip": "10.8.0.1", "port": 51820,
    }), mock.patch("app.services.awg.run_cmd", side_effect=run), mock.patch("app.services.awg.log_event") as event:
        success, message = await AWGService.sync_server_config(db, str(path))
    assert not success and "SECRET" not in message and "SECRET" not in str(event.call_args)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    assert sync_contents == (["new-runtime", runtime] if failure == "sync" else [])
