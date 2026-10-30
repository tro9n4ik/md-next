"""Индивидуальный MTProto: секреты храню зашифрованными, выход — только через ноду."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.crypto import decrypt_secret
from app.services.client_limits import access_allowed

PORT = 8445
SOCKS_PORT = 10909
STATS_PORT = 10910
_lock = asyncio.Lock()


def new_secret():
    return secrets.token_hex(16)


def client_link(profile, settings):
    key = decrypt_secret(profile.private_key_enc or "")
    host = settings.get("protocol.reality.server_address", os.getenv("SERVER_HOST", ""))
    domain = settings.get("mtproto.domain", os.getenv("SERVER_HOST", ""))
    if not key or not host or not domain:
        return ""
    secret = "ee" + key + domain.encode("ascii").hex()
    return "tg://proxy?" + urlencode({"server": host, "port": settings.get("profiles.port.mtproto", str(PORT)), "secret": secret})


async def sync_config(db):
    """Вызываю вместе с синхронизацией Xray; выключенный новый компонент не запускаю."""
    from app.services.privileges import enabled, call
    async with _lock:
        values = {row.key: row.value for row in (await db.execute(select(Setting))).scalars()}
        active = values.get("profiles.enabled.mtproto", "false").lower() in {"true", "1", "yes"}
        stage = Path(os.getenv("MTPROTO_STAGE_DIR", "/var/lib/md-next/app-config"))
        if not active and not (stage / "mtproto.json").exists():
            return True, "MTProto выключен"
        users = {}
        if active:
            rows = (await db.execute(select(ClientProfile, Client).join(Client).where(ClientProfile.kind == "mtproto"))).all()
            for profile, client in rows:
                if profile.is_enabled and access_allowed(client):
                    key = decrypt_secret(profile.private_key_enc or "")
                    if not key:
                        return False, "Не удалось расшифровать секрет MTProto. Перевыпустите профиль клиента."
                    users[f"p{profile.id}"] = key
        payload = {"port": int(values.get("profiles.port.mtproto", PORT)),
                   "domain": values.get("mtproto.domain", os.getenv("SERVER_HOST", "")), "users": users}
        try:
            stage.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".mtproto-", dir=stage)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(payload, stream)
                os.replace(temporary, stage / "mtproto.json")
            finally:
                Path(temporary).unlink(missing_ok=True)
            if enabled():
                code, _, _ = await call("mtproto-apply", timeout=120)
            elif os.geteuid() == 0:
                from app.services.shell import run_cmd
                script = Path(__file__).resolve().parents[3] / "scripts/mtproto-runtime.py"
                code, _, _ = await run_cmd("python3", str(script), "--apply", timeout=115)
            else:
                return False, "Для MTProto требуется системный помощник новой версии панели"
            return (False, "Не удалось применить MTProto. Проверьте свободный TCP-порт, доступ панели к GitHub и журнал md-next-mtproto.") if code else (True, "MTProto применён")
        except (OSError, RuntimeError, ValueError):
            return False, "Не удалось применить MTProto. Проверьте установку компонента и свободный TCP-порт."


async def collect_deltas(db):
    """Курсор в БД сохраняю в одной транзакции с начислением трафика."""
    async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
        response = await client.get(f"http://127.0.0.1:{STATS_PORT}/stats")
        response.raise_for_status()
        if len(response.content) > 4 * 1024 * 1024:
            raise ValueError("Слишком большой ответ статистики MTProto")
        data = response.json()
    if not isinstance(data.get("started_at"), str) or not isinstance(data.get("users"), dict):
        raise ValueError("Некорректная статистика MTProto")
    cursor = await db.get(Setting, "mtproto.traffic_cursor")
    previous = json.loads(cursor.value) if cursor else {}
    counters, deltas = {}, {}
    for key, value in data["users"].items():
        if not key.startswith("p") or not key[1:].isdecimal():
            continue
        current = [value.get("bytes_in"), value.get("bytes_out")]
        if any(type(n) is not int or not 0 <= n <= 2**63 - 1 for n in current):
            raise ValueError("Некорректный счётчик MTProto")
        old = previous.get("users", {}).get(key, [0, 0]) if previous.get("started_at") == data["started_at"] else [0, 0]
        deltas[int(key[1:])] = tuple(n - before if n >= before else n for n, before in zip(current, old))
        counters[key] = current
    content = json.dumps({"started_at": data["started_at"], "users": counters})
    if cursor:
        cursor.value = content
    else:
        db.add(Setting(key="mtproto.traffic_cursor", value=content))
    return deltas
