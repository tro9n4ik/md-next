"""Каркас CDN: отдельная ссылка на существующий XHTTP/TLS без изменения входов."""
import re
from urllib.parse import quote, urlencode


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
    domain = validate_domain(settings.get("cdn.domain", ""))
    if not domain:
        return ""
    params = {"encryption": "none", "security": "tls", "sni": domain,
              "host": domain, "type": "xhttp", "mode": "packet-up",
              "path": settings.get("profiles.path.vless_xhttp_tls", "/md-next-xhttp")}
    name = quote(f"{client.name} · CDN (тест)", safe="")
    return f"vless://{profile.uuid}@{domain}:443?{urlencode(params)}#{name}"
