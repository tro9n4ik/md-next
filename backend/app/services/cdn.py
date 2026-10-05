"""Каркас CDN: отдельная ссылка на существующий XHTTP/TLS без изменения входов."""
import re
import asyncio
import ipaddress
import socket
import uuid
import os
import json
import base64
from app.services.client_limits import cdn_quota_exhausted
from urllib.parse import quote, urlencode


def cdn_path(tls_path: str) -> str:
    return tls_path.rstrip("/") + "/cdn-get"


def cdn_transport() -> dict:
    return {"uplinkHTTPMethod": "GET", "uplinkDataPlacement": "header",
            "scMaxEachPostBytes": 2048, "uplinkChunkSize": 1000,
            "xPaddingBytes": "100-200"}


def cdn_access_allowed(client, profile, settings: dict) -> bool:
    value = settings.get(f"client.cdn.{getattr(client, 'id', '')}")
    return value == "true" if value is not None else getattr(profile, "is_enabled", True)


def validate_domain(value: str) -> str:
    value = value.strip().lower()
    if not value:
        return ""
    labels = value.split(".")
    if len(value) > 253 or len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ) or labels[-1].isdigit():
        raise ValueError("Укажите домен CDN без протокола, порта и пути")
    return value


def make_cdn_link(client, profile, settings: dict[str, str], *, preview: bool = False) -> str:
    if profile.kind != "vless_xhttp_tls" or not profile.uuid:
        return ""
    if not preview and settings.get("cdn.enabled", "false") != "true":
        return ""
    if not preview and not cdn_access_allowed(client, profile, settings):
        return ""
    if not preview and cdn_quota_exhausted(client):
        return ""
    domain = validate_domain(settings.get("cdn.domain", ""))
    if not domain:
        return ""
    params = {"encryption": "none", "security": "tls", "sni": domain,
              "host": domain, "type": "xhttp", "mode": "packet-up",
              "path": cdn_path(settings.get("profiles.path.vless_xhttp_tls", "/md-next-xhttp")),
              "extra": json.dumps(cdn_transport(), separators=(",", ":")), "alpn": "h2"}
    name = quote(f"{client.name} · Обход БС · CDN", safe="")
    return f"vless://{profile.uuid}@{domain}:443?{urlencode(params)}#{name}"


async def probe_cdn(domain: str, path: str) -> dict:
    """Проверка TLS и XHTTP GET без тела. Не заменяет тест VPN с телефона.

    Адрес фиксируется в curl: внутренние адреса и перенаправления исключены.
    """
    domain = validate_domain(domain)
    if not domain:
        return {"ok": False, "message": "Укажите домен CDN."}
    if not path.startswith("/") or any(c in path for c in "?#\r\n"):
        return {"ok": False, "message": "Некорректный путь XHTTP на сервере."}
    try:
        records = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, domain, 443, socket.AF_INET, socket.SOCK_STREAM), 5)
        addresses = sorted({r[4][0] for r in records})
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            return {"ok": False, "message": "Домен CDN должен указывать на публичный адрес."}
        async def request(suffix, upload=False):
            args = ["curl", "--silent", "--show-error", "--noproxy", "*", "--proto", "=https",
                    "--max-time", "10", "--connect-timeout", "5", "--max-redirs", "0",
                    "--resolve", f"{domain}:443:{addresses[0]}", "--output", os.devnull, "--write-out", "%{http_code}"]
            if upload:
                payload = base64.urlsafe_b64encode(b"md-next-cdn-probe").decode().rstrip("=")
                args += ["--header", "X-Data-0: " + payload,
                         "--header", f"Referer: https://{domain}{cdn_path(path)}/?x_padding=" + "X" * 150]
            args += [f"https://{domain}{suffix}"]
            process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                data, _ = await asyncio.wait_for(process.communicate(), 12)
                if process.returncode:
                    raise ValueError("TLS or network error")
                return int(data.rsplit(b"\n", 1)[-1])
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        get_status = await request("/")
        upload_status = await request(cdn_path(path) + "/" + str(uuid.uuid4()) + "/0", True)
        ok = get_status == 200 and upload_status == 200
        return {"ok": ok, "get_status": get_status, "upload_status": upload_status,
                "message": "HTTPS и отправка XHTTP GET проходят. Обновите подписку и проверьте профиль в Happ и ограниченной сети." if ok else
                f"CDN вернул HTTPS: {get_status}, XHTTP GET: {upload_status}. Проверьте маршрут CDN и передачу заголовков."}
    except (OSError, ValueError, asyncio.TimeoutError):
        return {"ok": False, "message": "Не удалось проверить CDN. Проверьте DNS, сертификат HTTPS и доступность источника."}
