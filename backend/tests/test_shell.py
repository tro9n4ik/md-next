import sys

import pytest

from app.services.shell import CommandTimeout, CommandUnavailable, run_cmd


@pytest.mark.asyncio
async def test_run_cmd_success():
    code, stdout, stderr = await run_cmd(sys.executable, "-c", "print('ready')")
    assert code == 0
    assert stdout.strip() == "ready"
    assert stderr == ""


@pytest.mark.asyncio
async def test_run_cmd_missing_binary_uses_russian_runtime_error():
    with pytest.raises(CommandUnavailable, match="не найдена в PATH"):
        await run_cmd("md-next-command-that-does-not-exist")


@pytest.mark.asyncio
async def test_run_cmd_timeout_kills_process():
    with pytest.raises(CommandTimeout, match="превысила время ожидания"):
        await run_cmd(sys.executable, "-c", "import time; time.sleep(10)", timeout=0.05)


@pytest.mark.asyncio
async def test_restricted_path_still_finds_explicit_python_executable(tmp_path):
    code, stdout, _ = await run_cmd(
        sys.executable,
        "-c",
        "import os; print(bool(os.environ.get('PATH'))) ",
        env={"PATH": str(tmp_path)},
    )
    assert code == 0
    assert stdout.strip() == "True"


@pytest.mark.asyncio
async def test_untrusted_argument_is_passed_literally_not_executed(tmp_path):
    marker = tmp_path / 'injected'
    payload = f"peer; $(touch {marker}) & echo unsafe"
    code, stdout, _ = await run_cmd(sys.executable, '-c', 'import sys; print(sys.argv[1])', payload)
    assert code == 0
    assert stdout.strip() == payload
    assert not marker.exists()
