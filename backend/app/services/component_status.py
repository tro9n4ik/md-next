"""Read-only inventory of components installed on the panel server."""

import asyncio
import re
import sys
from pathlib import Path

from app.services.shell import CommandTimeout, CommandUnavailable, find_command, run_cmd

# Fixed commands only: the API never accepts a program, path or service name.
COMPONENTS = (
    ("xray", "Xray-core", ("xray", "version"), "xray", "VLESS, XHTTP и Hysteria 2"),
    ("adguard", "AdGuard Home", ("/opt/md-next-adguard/AdGuardHome", "--version"), "md-next-adguard", "Фильтрация DNS для клиентов с AdBlock"),
    ("nginx", "Nginx", ("nginx", "-v"), "nginx", "Сайт, HTTPS и проксирование панели"),
    ("awg", "AmneziaWG Tools", ("awg", "--version"), None, "Инструменты туннеля AmneziaWG"),
    ("warp", "Cloudflare WARP", ("warp-cli", "--version"), "warp-svc", "Локальная служба; WARP на ноде проверяется отдельно"),
    ("fail2ban", "Fail2ban", ("fail2ban-client", "--version"), "fail2ban", "Защита от перебора паролей и сканирования"),
    ("certbot", "Certbot", ("certbot", "--version"), None, "Выпуск и продление HTTPS-сертификатов"),
    ("node", "Node.js", ("node", "--version"), None, "Сборка интерфейса при обновлении"),
)


def _version(output: str) -> str | None:
    match = re.search(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+){0,2}(?:[-+][A-Za-z0-9.]+)?)(?![\w.])", output[:2048])
    return match.group(1) if match else None


async def _probe(spec: tuple) -> dict:
    key, name, command, service, description = spec
    result = {"key": key, "name": name, "version": None, "status": "unknown", "description": description}
    if not find_command(command[0]):
        result["status"] = "not_installed"
        return result
    try:
        code, stdout, stderr = await run_cmd(*command, timeout=3, env={"LC_ALL": "C"})
        if code == 0:
            result["version"] = _version(stdout + "\n" + stderr)
        if service:
            _, active, _ = await run_cmd("systemctl", "is-active", service, timeout=3, env={"LC_ALL": "C"})
            result["status"] = {"active": "running", "inactive": "stopped", "failed": "failed",
                                "activating": "starting", "deactivating": "stopping"}.get(active.strip(), "unknown")
        elif key == "awg":
            # awg --version identifies the tools, not the kernel module.
            result["status"] = "running" if Path("/sys/class/net/awg0").exists() else "stopped"
            module = Path("/sys/module/amneziawg/version")
            try:
                module_version = _version(module.read_text(encoding="utf-8").strip()) if module.exists() else None
                if module_version:
                    result["description"] += f" · модуль {module_version}"
            except OSError:
                pass
        else:
            result["status"] = "installed"
    except (CommandUnavailable, CommandTimeout, OSError):
        pass
    return result


async def get_components() -> dict:
    components = await asyncio.gather(*(_probe(spec) for spec in COMPONENTS))
    components.append({"key": "python", "name": "Python", "version": sys.version.split()[0],
                       "status": "running", "description": "Среда выполнения API и Telegram-бота"})
    return {"scope": "panel_server", "components": components}
