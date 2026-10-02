import asyncio
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Optional

import httpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import Setting
from app.models.node import Node

from app.services.shell import run_cmd, find_command

logger = logging.getLogger(__name__)
_setup_lock = asyncio.Lock()


class WarpService:
    DEFAULT_PORT = 40000
    COMMAND_TIMEOUT = 12
    REGISTRATION_TIMEOUT = 30
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
        mode_match = re.search(r"(?:mode|service mode)\s*:\s*(warpproxy|proxy|warpwithdns(?:\s+over\s+https|overhttps)|warp(?:\+doh)?|doh)\b", status_output, re.I)
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
            _, settings_output, _ = await cls._run_process("settings", timeout=cls.STATUS_TIMEOUT)
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Не удалось получить состояние warp-cli: %s", exc)
            return {
                "installed": True, "service_active": service_active, "registered": False,
                "state": "Disconnected", "mode": "unknown", "port": await cls.get_port(db),
                "instruction": "Не удалось получить состояние warp-cli; проверьте службу warp-svc.",
            }
        parsed = cls.parse_status(f"{status_output or status_error}\n{settings_output}", registration_output or registration_error, reg_code == 0)
        parsed["runtime_port"] = parsed["port"]
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
    async def register(cls, db: AsyncSession | None = None) -> tuple[bool, str]:
        try:
            if db is not None:
                # Уже созданную регистрацию не заменяем повторной командой.
                code, stdout, stderr = await cls._run_process("registration", "show")
                if code == 0:
                    return True, "Регистрация WARP уже существует."
                if not re.search(r"missing|not\s+(?:found|registered)|does not exist|registration\s+required", stdout + stderr, re.I):
                    return False, "Не удалось проверить существующую регистрацию WARP."
                nodes = (await db.execute(select(Node).where(Node.is_enabled.is_(True), Node.status != "unhealthy").order_by(Node.priority, Node.id))).scalars().all()
                selected = await db.get(Setting, "active_node_id")
                nodes.sort(key=lambda node: str(node.id) != (selected.value if selected else ""))
                node = next((node for node in nodes if node.secret), None)
                if node:
                    return await cls._register_via_node(node.id)
            success, message = await cls._run_command("registration", "new", timeout=cls.REGISTRATION_TIMEOUT)
            if not success and re.search(r"unknown\s+(?:command|subcommand)|unrecognized", message, re.I):
                success, message = await cls._run_command("register", timeout=cls.REGISTRATION_TIMEOUT)
            if not success and "failed to communicate with the warp api" in message.lower():
                message = "Не удалось связаться с регистрационным API Cloudflare. Лицензионный ключ не требуется; проверьте доступность API с сервера."
            return success, message
        except (OSError, RuntimeError, TimeoutError) as exc:
            logger.warning("Ошибка регистрации WARP: %s", exc)
            return False, str(exc)

    @classmethod
    async def _register_via_node(cls, node_id: int) -> tuple[bool, str]:
        """Отдельная служба удаляет временные правила даже при остановке панели."""
        helper = Path(__file__).with_name("warp_registration.py")
        config = os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json")
        code, _, _ = await run_cmd(
            "systemd-run", "--quiet", "--wait", "--collect", "--pipe",
            "--unit=md-next-warp-registration", "--property=RuntimeMaxSec=60",
            "--property=TimeoutStopSec=30", "--property=KillMode=control-group",
            f"--property=ExecStopPost={sys.executable} {helper} --cleanup",
            sys.executable, str(helper), "--node-id", str(node_id), "--config", config,
            timeout=100,
        )
        if code:
            return False, "Не удалось зарегистрировать WARP через ноду. Проверьте её доступность и журнал md-next-warp-registration."
        logger.info("Бесплатный WARP зарегистрирован через ноду %s", node_id)
        return True, "Бесплатный WARP зарегистрирован через ноду."

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
    async def set_mode(cls, db: AsyncSession, mode: str, port: int, *, persist: bool = True) -> tuple[bool, str]:
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
            if persist:
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
    async def test_proxy(cls, port: int, *, timeout: float = 15.0) -> dict[str, str]:
        proxy = f"socks5://127.0.0.1:{port}"
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout) as client:
            response = await client.get("https://www.cloudflare.com/cdn-cgi/trace")
            response.raise_for_status()
        values = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line)
        return {"ip": values.get("ip", ""), "country": values.get("loc", ""), "warp": values.get("warp", "off")}

    @classmethod
    async def setup_warp_proxy(cls, db: AsyncSession) -> tuple[bool, str]:
        """Включает бесплатный WARP через SOCKS5, сохраняя существующую регистрацию."""
        async with _setup_lock:
            return await cls._setup_warp_proxy(db)

    @classmethod
    async def _setup_warp_proxy(cls, db: AsyncSession) -> tuple[bool, str]:
        previous = await cls.status(db)
        if not previous["installed"]:
            return False, "Установите пакет cloudflare-warp. Лицензия для обычного WARP не требуется."
        started_service = not previous["service_active"]
        configured = False
        try:
            if started_service:
                code, _, _ = await run_cmd("systemctl", "start", "warp-svc", timeout=cls.COMMAND_TIMEOUT)
                if code:
                    return False, "Не удалось запустить службу warp-svc."
                previous = {**await cls.status(db), "service_active": False}
            # Прокси-режим задаётся до регистрации: общий маршрут сервера не меняется.
            port = await cls.get_port(db)
            configured = True
            ok, reason = await cls.set_mode(db, "proxy", port, persist=False)
            if not ok:
                raise RuntimeError(reason)
            if not previous["registered"]:
                code, stdout, stderr = await cls._run_process("registration", "show")
                if code and not re.search(r"missing|not\s+(?:found|registered)|does not exist|registration\s+required", stdout + stderr, re.I):
                    raise RuntimeError("Не удалось проверить регистрацию WARP; существующая учётная запись сохранена.")
                if code:
                    ok, reason = await cls.register(db)
                    if not ok:
                        raise RuntimeError(reason)
            ok, reason = await cls.connect()
            if not ok:
                raise RuntimeError(reason)
            for attempt in range(5):
                try:
                    trace = await cls.test_proxy(port, timeout=5.0)
                    if trace["warp"] in {"on", "plus"}:
                        # Настройки сохраняются только после проверки реального выхода.
                        for key, value in (("warp.mode", "proxy"), ("warp.proxy_port", str(port))):
                            setting = (await db.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
                            if setting is None:
                                db.add(Setting(key=key, value=value))
                            else:
                                setting.value = value
                        await db.commit()
                        return True, "WARP включён без лицензии. Выберите нужные готовые правила."
                except (httpx.HTTPError, OSError):
                    pass
                if attempt < 4:
                    await asyncio.sleep(1)
            raise RuntimeError("Прокси WARP не прошёл проверку подключения. Повторите включение или проверьте журнал warp-svc.")
        except Exception as exc:
            await db.rollback()
            if configured:
                if previous["state"] != "Connected":
                    await cls.disconnect()
                if previous["mode"] in {"proxy", "warp"}:
                    restored, _ = await cls.set_mode(db, previous["mode"], previous.get("runtime_port", previous["port"]), persist=False)
                    if not restored:
                        logger.error("Не удалось восстановить прежний режим WARP")
                if previous["state"] == "Connected":
                    await cls.connect()
            if started_service:
                await run_cmd("systemctl", "stop", "warp-svc", timeout=cls.COMMAND_TIMEOUT)
            return False, str(exc)

    @classmethod
    async def fetch_via_warp(cls, url: str, method: str = "GET", headers: Optional[dict[str, str]] = None, json_data: Optional[dict[str, Any]] = None, port: Optional[int] = None) -> httpx.Response:
        proxy_port = port or cls.DEFAULT_PORT
        async with httpx.AsyncClient(proxy=f"socks5://127.0.0.1:{proxy_port}", timeout=15.0) as client:
            if method.upper() == "POST":
                return await client.post(url, headers=headers, json=json_data)
            return await client.get(url, headers=headers)
