"""Безопасный запуск системных команд с предсказуемым PATH и таймаутом."""

import asyncio
import os
import shutil
from typing import Mapping


SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_COMMAND_NAMES = {
    "xray": "Xray",
    "systemctl": "systemctl",
    "nginx": "Nginx",
    "awg": "AmneziaWG (awg)",
    "awg-quick": "AmneziaWG (awg-quick)",
    "warp-cli": "WARP (warp-cli)",
}


class CommandUnavailable(RuntimeError):
    """Исполняемый файл команды отсутствует в PATH."""


class CommandTimeout(RuntimeError):
    """Внешняя команда не завершилась за отведённое время."""


def _command_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    result = os.environ.copy()
    if env:
        result.update(env)
    existing = result.get("PATH", "")
    paths = [part for part in existing.split(os.pathsep) if part]
    paths.extend(part for part in SYSTEM_PATH.split(":") if part not in paths)
    result["PATH"] = os.pathsep.join(paths)
    return result


def find_command(command: str, env: Mapping[str, str] | None = None) -> str | None:
    """Найти программу с теми же системными путями, что использует run_cmd."""
    command_env = _command_env(env)
    return shutil.which(command, path=command_env["PATH"])


async def run_cmd(
    *args: str,
    timeout: float = 10,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Запустить команду и вернуть код завершения, stdout и stderr."""
    if not args:
        raise ValueError("Не задана команда для запуска")

    from app.services.privileges import enabled, call
    if enabled() and (args[0] in {'systemctl', 'awg', 'awg-quick', 'systemd-run'} or
                      args[0] == 'xray' and len(args) > 2 and args[1:3] == ('run', '-test') or
                      args[0] == 'python3' and len(args) > 1 and args[1].endswith('/awg-routing.py')):
        return await call('command', args=list(args), timeout=timeout + 5)

    command_env = _command_env(env)
    binary = find_command(args[0], command_env)
    display_name = _COMMAND_NAMES.get(args[0], args[0])
    if not binary:
        raise CommandUnavailable(f"Команда {display_name} не найдена в PATH")

    process = await asyncio.create_subprocess_exec(
        binary,
        *args[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=command_env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise CommandTimeout(f"Команда {display_name} превысила время ожидания ({timeout:g} с)") from exc

    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
