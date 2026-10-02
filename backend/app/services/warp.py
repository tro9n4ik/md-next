import logging
import re
from typing import Any, Optional

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import Setting

from app.services.shell import run_cmd, find_command

logger = logging.getLogger(__name__)


class WarpService:
    DEFAULT_PORT = 40000
    COMMAND_TIMEOUT = 12
    STATUS_TIMEOUT = 6

    @classmethod
    def cli_path(cls) -> Optional[str]:
        return find_command("warp-cli")

    @classmethod
    async def _run_process(cls, *args: str, timeout: Optional[float] = None) -> tuple[int, str, str]:
        if not cls.cli_path():
            from app.services.shell import CommandUnavailable
            raise CommandUnavailable("Команда WARP (warp-cli) не найдена в PATH")
        return await run_cmd("warp-cli", "--accept-tos", *args, timeout=timeout or cls.COMMAND_TIMEOUT)

    @classmethod
    async def _run_command(cls, *args: str, timeout: Optional[float] = None) -> tuple[bool, str]:
        code, stdout, stderr = await cls._run_process(*args, timeout=timeout)
        text = (stdout or stderr).strip()
        if code:
            return False, text or "Ошибка выполнения команды warp-cli"
        return True, text or "OK"

    @classmethod
    async def _systemd_active(cls) -> bool:
        try:
            code, _, _ = await run_cmd("systemctl", "is-active", "--quiet", "warp-svc", timeout=cls.STATUS_TIMEOUT)
            return code == 0
        except RuntimeError:
            return False

    @classmethod
    def parse_status(cls, status_output: str, registration_output: str = "", cli_ok: bool = True) -> dict[str, Any]:
        combined = f"{status_output}\n{registration_output}"
        status_match = re.search(r"(?:status(?:\s+update)?|connection\s+status)\s*:\s*(connected|disconnected|connecting)", status_output, re.I)
        state = status_match.group(1).capitalize() if status_match else "Disconnected"
        mode_match = re.search(r"(?:mode|service mode)\s*:\s*(proxy|warp(?:\+doh)?|doh|warpwithdns over https|warpproxy)", status_output, re.I)
        mode_raw = mode_match.group(1).lower() if mode_match else "unknown"
        mode = "proxy" if mode_raw in {"proxy", "warpproxy"} else "warp" if mode_raw.startswith("warp") else mode_raw
        port_match = re.search(r"(?:127\.0\.0\.1|localhost)\s*:\s*(\d{1,5})|(?:proxy(?:\s+listener)?\s+port)\s*:\s*(\d{1,5})", status_output, re.I)
        port = int(next(value for value in port_match.groups() if value)) if port_match else cls.DEFAULT_PORT
        negative_registration = re.search(r"registration\s+(?:missing|not\s+found|not\s+registered)|not\s+registered|registration\s+required", combined, re.I)
        positive_registration = re.search(
            r"registration\s*:\s*(?:success|registered)|registration\s+status\s*:\s*registered|"
            r"registered\s*:\s*(?:yes|true)|account\s+type\s*:|device\s+id\s*:",
            registration_output, re.I,
        )
        registered = bool(cli_ok and positive_registration and not negative_registration)
        return {"state": state, "mode": mode, "port": port, "registered": registered}

    @classmethod
    async def status(cls, db: AsyncSession) -> dict[str, Any]:
        cli = cls.cli_path()
        if not cli:
            return {
                "installed": False, "service_active": False, "registered": False,
                "state": "Disconnected", "mode": "unknown", "port": await cls.get_port(db),
                "instruction": "Установите пакет cloudflare-warp и повторите проверку.",
            }
        service_active = await cls._systemd_active()
        try:
            status_code, status_output, status_error = await cls._run_process("status", timeout=cls.STATUS_TIMEOUT)
            reg_code, registration_output, registration_error = await cls._run_process("registration", "show", timeout=cls.STATUS_TIMEOUT)
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Не удалось получить состояние warp-cli: %s", exc)
            return {
                "installed": True, "service_active": service_active, "registered": False,
                "state": "Disconnected", "mode": "unknown", "port": await cls.get_port(db),
                "instruction": "Не удалось получить состояние warp-cli; проверьте службу warp-svc.",
            }
        parsed = cls.parse_status(status_output or status_error, registration_output or registration_error, reg_code == 0)
        parsed["port"] = await cls.get_port(db, parsed["port"])
        parsed.update({"installed": True, "service_active": service_active, "instruction": None})
        if status_code != 0:
            parsed["state"] = "Disconnected"
        return parsed

    @classmethod
    async def get_port(cls, db: AsyncSession, fallback: Optional[int] = None) -> int:
        result = await db.execute(select(Setting).where(Setting.key == "warp.proxy_port"))
        setting = result.scalar_one_or_none()
        try:
            return int(setting.value) if setting else int(fallback or cls.DEFAULT_PORT)
        except (TypeError, ValueError):
            return cls.DEFAULT_PORT

    @classmethod
    async def _save_setting(cls, db: AsyncSession, key: str, value: str) -> None:
        setting = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
        if setting is None:
            db.add(Setting(key=key, value=value))
        else:
            setting.value = value
        await db.commit()

    @classmethod
    async def register(cls) -> tuple[bool, str]:
        try:
            success, message = await cls._run_command("registration", "new")
            if not success and re.search(r"unknown\s+(?:command|subcommand)|unrecognized", message, re.I):
                success, message = await cls._run_command("register")
            return success, message
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Ошибка регистрации WARP: %s", exc)
            return False, str(exc)

    @classmethod
    async def connect(cls) -> tuple[bool, str]:
        try:
            return await cls._run_command("connect")
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Ошибка подключения WARP: %s", exc)
            return False, str(exc)

    @classmethod
    async def disconnect(cls) -> tuple[bool, str]:
        try:
            return await cls._run_command("disconnect")
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Ошибка отключения WARP: %s", exc)
            return False, str(exc)

    @classmethod
    async def set_mode(cls, db: AsyncSession, mode: str, port: int) -> tuple[bool, str]:
        cli_mode = "proxy" if mode == "proxy" else "warp+doh"
        try:
            success, message = await cls._run_command("mode", cli_mode)
            if not success and re.search(r"unknown\s+(?:command|subcommand)|unrecognized", message, re.I):
                success, message = await cls._run_command("set-mode", mode)
            if not success:
                return False, message
            success, message = await cls._run_command("proxy", "port", str(port))
            if not success and re.search(r"unknown\s+(?:command|subcommand)|unrecognized", message, re.I):
                success, message = await cls._run_command("set-proxy-port", str(port))
            if not success:
                return False, message
            await cls._save_setting(db, "warp.mode", mode)
            await cls._save_setting(db, "warp.proxy_port", str(port))
            return True, message
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Не удалось изменить режим WARP: %s", exc)
            return False, str(exc)

    @classmethod
    async def set_license(cls, license_key: str) -> tuple[bool, str]:
        try:
            # Не записываем ключ в журнал и не включаем его в исключения или ответы.
            code, stdout, _ = await cls._run_process("registration", "license", license_key)
            if code:
                return False, "Cloudflare не принял лицензионный ключ WARP+. Проверьте ключ и регистрацию."
            return True, (stdout.strip().replace(license_key, "[redacted]") or "Лицензия WARP+ применена.")
        except (OSError, RuntimeError, TimeoutError):
            logger.warning("Не удалось применить лицензию WARP+")
            return False, "Не удалось применить лицензионный ключ WARP+. Проверьте warp-svc и регистрацию."

    @classmethod
    async def test_proxy(cls, port: int) -> dict[str, str]:
        proxy = f"socks5://127.0.0.1:{port}"
        async with httpx.AsyncClient(proxy=proxy, timeout=15.0) as client:
            response = await client.get("https://www.cloudflare.com/cdn-cgi/trace")
            response.raise_for_status()
        values = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line)
        return {"ip": values.get("ip", ""), "country": values.get("loc", ""), "warp": values.get("warp", "off")}

    @classmethod
    async def setup_warp_proxy(cls, db: AsyncSession) -> tuple[bool, str]:
        ok, reason = await cls.register()
        if not ok and "already registered" not in reason.lower():
            return False, reason
        ok, reason = await cls.set_mode(db, "proxy", await cls.get_port(db))
        if not ok:
            return False, reason
        return await cls.connect()

    @classmethod
    async def fetch_via_warp(cls, url: str, method: str = "GET", headers: Optional[dict[str, str]] = None, json_data: Optional[dict[str, Any]] = None, port: Optional[int] = None) -> httpx.Response:
        proxy_port = port or cls.DEFAULT_PORT
        async with httpx.AsyncClient(proxy=f"socks5://127.0.0.1:{proxy_port}", timeout=15.0) as client:
            if method.upper() == "POST":
                return await client.post(url, headers=headers, json=json_data)
            return await client.get(url, headers=headers)
