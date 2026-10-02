import os
import re
import tempfile
import asyncio
import stat
import logging

from app.services.shell import run_cmd

logger = logging.getLogger(__name__)
_nginx_lock = asyncio.Lock()


async def _write_and_reload(config_path: str, original: str, updated: str) -> None:
    if original == updated:
        return
    fd, temp_path = tempfile.mkstemp(prefix="md-next-nginx-", dir=os.path.dirname(config_path), text=True)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(updated)
        os.chmod(temp_path, stat.S_IMODE(os.stat(config_path).st_mode))
        os.replace(temp_path, config_path)
        replaced = True
        code, out, err = await run_cmd("nginx", "-t", timeout=15)
        if code:
            raise RuntimeError(err or out or "Проверка конфигурации Nginx завершилась с ошибкой")
        code, out, err = await run_cmd("systemctl", "reload", "nginx", timeout=15)
        if code:
            raise RuntimeError(err or out or "Не удалось перезагрузить Nginx")
    except Exception:
        if replaced:
            with open(config_path, "w", encoding="utf-8") as target:
                target.write(original)
            try:
                await run_cmd("systemctl", "reload", "nginx", timeout=15)
            except Exception:
                logger.exception("Не удалось перезагрузить Nginx после отката")
        raise
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


async def apply_reality_sni(server_name: str) -> None:
    """Route Reality's SNI to Xray, preserving the panel and existing aliases."""
    config_path = os.getenv("NGINX_STREAM_CONFIG", "/etc/nginx/stream-available/md-next-stream.conf")
    if not os.path.isfile(config_path):
        return
    server_name = server_name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", server_name):
        raise ValueError("Укажите непустой домен Reality SNI")
    async with _nginx_lock:
        with open(config_path, encoding="utf-8") as source:
            original = source.read()
        mapping = re.search(r"map\s+\$ssl_preread_server_name\s+\$backend_name\s*\{(?P<body>[^{}]*)\}", original)
        if not mapping:
            raise RuntimeError("Не найдена таблица маршрутизации SNI в Nginx")
        body = mapping.group("body")
        entry = re.search(r"(?mi)^\s*" + re.escape(server_name) + r"\s+(\S+)\s*;", body)
        if entry:
            if entry.group(1) != "xray_backend":
                raise ValueError("Этот SNI занят другим сервисом Nginx; используйте отдельный домен Reality")
            return
        updated = original[:mapping.end("body")] + f"    {server_name} xray_backend;\n" + original[mapping.end("body"):]
        await _write_and_reload(config_path, original, updated)


async def apply_xhttp_tls_path(path: str) -> None:
    async with _nginx_lock:
        await _apply_xhttp_tls_path(path)


async def _apply_xhttp_tls_path(path: str) -> None:
    config_path = os.getenv("NGINX_PANEL_CONFIG", "/etc/nginx/sites-available/md-next.conf")
    if not os.path.isfile(config_path):
        return
    with open(config_path, "r", encoding="utf-8") as source:
        original = source.read()
    block = f"""    # MD_NEXT_XHTTP_TLS_BEGIN
    location {path} {{
        proxy_pass http://127.0.0.1:8446;
        proxy_http_version 1.1;
        proxy_request_buffering off;
        client_max_body_size 0;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
    # MD_NEXT_XHTTP_TLS_END"""
    marker = re.compile(r"\s*# MD_NEXT_XHTTP_TLS_BEGIN.*?# MD_NEXT_XHTTP_TLS_END", re.S)
    if marker.search(original):
        updated = marker.sub("\n" + block, original)
    else:
        server = re.search(r"(?s)(server\s*\{.*?listen\s+127\.0\.0\.1:8080\b.*?)(\n\s*location\s+/\s*\{)", original)
        if not server:
            raise RuntimeError("Не найден server-блок заглушки Nginx для XHTTP TLS")
        updated = original[:server.start(2)] + "\n" + block + original[server.start(2):]
    await _write_and_reload(config_path, original, updated)
