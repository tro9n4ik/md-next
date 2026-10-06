import os
import shutil
import base64
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization

from app.services.xray import XrayService


@pytest.mark.asyncio
async def test_apply_config_format_json_flag(tmp_path):
    calls = [
        (0, "ok", ""),
        (0, "", ""),
        (0, "active", ""),
    ]

    async def fake_run_cmd(*args, **kwargs):
        return calls.pop(0)

    with mock.patch.dict(os.environ, {"XRAY_PRIVATE_KEY": "test_key", "XRAY_SERVER_NAME": "test.com"}):
        with mock.patch("app.services.xray.run_cmd", side_effect=fake_run_cmd) as run_cmd:
            success, reason = await XrayService.apply_config(clients=[], config_path=str(tmp_path / "xray.json"))
    assert success is True
    assert run_cmd.call_args_list[0].args[:5] == ("xray", "run", "-test", "-format", "json")


@pytest.mark.asyncio
async def test_apply_config_missing_keys():
    with mock.patch.dict(os.environ, {"XRAY_PRIVATE_KEY": "", "XRAY_SERVER_NAME": ""}):
        success, reason = await XrayService.apply_config(clients=[], config_path="/tmp/test_xray.json")
    assert success is False
    assert "XRAY_PRIVATE_KEY" in reason


@pytest.mark.asyncio
async def test_apply_config_test_failure(tmp_path):
    async def fake_run_cmd(*args, **kwargs):
        return 1, "", "Ошибка синтаксиса"

    with mock.patch.dict(os.environ, {"XRAY_PRIVATE_KEY": "test_key", "XRAY_SERVER_NAME": "test.com"}):
        with mock.patch("app.services.xray.run_cmd", side_effect=fake_run_cmd):
            success, reason = await XrayService.apply_config(clients=[], config_path=str(tmp_path / "xray.json"))
    assert success is False
    assert "Ошибка синтаксиса" in reason


@pytest.mark.asyncio
async def test_apply_config_first_run_failure_cleans_up_file(tmp_path):
    target_path = str(tmp_path / "new_xray_config.json")
    responses = [(0, "ok", ""), (0, "", ""), (1, "inactive", "")]

    async def fake_run_cmd(*args, **kwargs):
        return responses.pop(0)

    with mock.patch.dict(os.environ, {"XRAY_PRIVATE_KEY": "test_key", "XRAY_SERVER_NAME": "test.com"}):
        with mock.patch("app.services.xray.run_cmd", side_effect=fake_run_cmd):
            success, reason = await XrayService.apply_config(clients=[], config_path=target_path)
    assert success is False
    assert "не активировалась" in reason
    assert not os.path.exists(target_path)


@pytest.mark.asyncio
async def test_apply_config_restart_failure_rollback(tmp_path):
    target = tmp_path / "xray.json"
    target.write_text("previous", encoding="utf-8")
    responses = [(0, "ok", ""), (0, "", ""), (1, "inactive", ""), (0, "", "")]

    async def fake_run_cmd(*args, **kwargs):
        return responses.pop(0)

    with mock.patch.dict(os.environ, {"XRAY_PRIVATE_KEY": "test_key", "XRAY_SERVER_NAME": "test.com"}):
        with mock.patch("app.services.xray.run_cmd", side_effect=fake_run_cmd):
            success, reason = await XrayService.apply_config(clients=[], config_path=str(target))
    assert success is False
    assert "не активировалась" in reason
    assert target.read_text(encoding="utf-8") == "previous"


@pytest.mark.skipif(shutil.which("xray") is None, reason="Бинарник Xray не найден на хосте")
@pytest.mark.asyncio
async def test_real_xray_test_format_json(tmp_path):
    from app.services.shell import run_cmd

    private_key = x25519.X25519PrivateKey.generate()
    private_key_b64 = base64.urlsafe_b64encode(
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode().rstrip("=")
    config_file = tmp_path / "cfg.json.tmp.12345"
    config_file.write_text(
        XrayService.generate_config(
            clients=[], server_private_key=private_key_b64, dest="127.0.0.1:8080", server_name="example.com"
        ),
        encoding="utf-8",
    )
    code, stdout, stderr = await run_cmd("xray", "run", "-test", "-format", "json", "-config", str(config_file))
    assert code == 0, stderr or stdout
