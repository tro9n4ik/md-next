import os
import re
import tempfile

from app.services.shell import run_cmd


async def apply_xhttp_tls_path(path: str) -> None:
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
    directory = os.path.dirname(config_path)
    fd, temp_path = tempfile.mkstemp(prefix="md-next-nginx-", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(updated)
        os.replace(temp_path, config_path)
        result_code, result_stdout, result_stderr = await run_cmd("nginx", "-t", timeout=15)
        if result_code:
            with open(config_path, "w", encoding="utf-8") as target:
                target.write(original)
            raise RuntimeError(result_stderr or result_stdout or "Проверка конфигурации Nginx завершилась с ошибкой")
        reload_code, reload_stdout, reload_stderr = await run_cmd("systemctl", "reload", "nginx", timeout=15)
        if reload_code:
            raise RuntimeError(reload_stderr or reload_stdout or "Не удалось перезагрузить Nginx")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
