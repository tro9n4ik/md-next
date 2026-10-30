"""Личная страница без доступа к административным API и данным других клиентов."""
import base64
import hashlib
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import qrcode
from qrcode.image.pure import PyPNGImage
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.services.client_limits import limit_info, utc
from app.services.cdn import cdn_access_allowed
from app.services.profiles import make_profile_data

environment = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
                          autoescape=select_autoescape(["html"]))

# Hash the static script source, never rendered client data. The browser hashes
# the UTF-8 text between script tags, including its leading/trailing newlines.
_template_source = environment.loader.get_source(environment, "subscription.html")[0]
_scripts = re.findall(r'<script>(.*?)</script>', _template_source, re.DOTALL)
if len(_scripts) != 1 or any(marker in _scripts[0] for marker in ('{{', '{%')):
    raise RuntimeError('Subscription page must contain exactly one static script')
_script_hash = base64.b64encode(hashlib.sha256(_scripts[0].encode('utf-8')).digest()).decode('ascii')
SUBSCRIPTION_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                    f"script-src 'sha256-{_script_hash}'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'none'")


def size(value):
    value = max(0, value or 0)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if value < 1024 or unit == "ТиБ":
            number = f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return f"{number} {unit}"
        value /= 1024


_CONNECTIONS = {
    "vless_reality_tcp": ("Reality TCP", "Основной профиль для повседневного подключения.", "globe"),
    "vless_xhttp_reality": ("XHTTP Reality", "Дополнительный вариант с маскировкой Reality.", "layers"),
    "vless_xhttp_tls": ("XHTTP TLS", "Альтернативное подключение через HTTPS.", "shield"),
    "hysteria2": ("Hysteria 2", "Вариант через UDP, если он доступен в вашей сети.", "bolt"),
    "awg": ("AmneziaWG", "Для подключения запросите у администратора файл .conf.", "key"),
    "mtproto": ("Telegram MTProto", "Отдельный прокси для подключения внутри Telegram.", "telegram"),
}


def render_subscription_page(client, profiles, settings, enabled, url, usage, total):
    limits = limit_info(client)
    reasons = {"disabled": "Приостановлена", "expired": "Срок истёк", "monthly_quota": "Лимит исчерпан"}
    status = reasons.get(limits["blocked_reason"], "Активна")
    cdn_available = any(p.kind == 'vless_xhttp_tls' and p.kind in enabled
                        and settings.get('cdn.enabled') == 'true' and cdn_access_allowed(client, p, settings)
                        for p in profiles)
    connections = [{"kind": kind, "name": title, "description": description, "icon": icon,
                    "available": True}
                   for kind, (title, description, icon) in _CONNECTIONS.items()
                   if kind in enabled and any(p.kind == kind and p.is_enabled for p in profiles)]
    if cdn_available:
        connections.append({"kind": "cdn", "name": "Обход БС", "icon": "shield",
                            "description": "Для сетей с ограничениями. Доступность зависит от сети.",
                            "available": not limits["cdn_quota_exhausted"]})
    vpn_connections = [c for c in connections if c["kind"] not in {"awg", "mtproto"} and c["available"]]
    blocked_message = {
        "disabled": "Доступ приостановлен администратором. Уточните у него условия возобновления подписки.",
        "expired": "Срок действия закончился. Для продления обратитесь к администратору — добавлять подписку заново не потребуется.",
        "monthly_quota": "Трафик за текущий период закончился. Дождитесь нового периода или уточните у администратора возможность увеличить лимит.",
    }.get(limits["blocked_reason"], "")
    buffer = BytesIO()
    qrcode.make(url, image_factory=PyPNGImage).save(buffer)
    expiry = utc(client.expires_at) if client.expires_at else None
    days = max(0, int((expiry - datetime.now(timezone.utc)).total_seconds() // 86400)) if expiry else None
    telegram = ""
    telegram_qr = ""
    if limits["access_allowed"] and "mtproto" in enabled:
        profile = next((p for p in profiles if p.kind == "mtproto" and p.is_enabled), None)
        if profile:
            telegram = make_profile_data(client, profile, settings)
            if telegram:
                image = BytesIO()
                qrcode.make(telegram, image_factory=PyPNGImage).save(image)
                telegram_qr = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    return environment.get_template("subscription.html").render(
        name=client.name, subscription_name=settings.get("subscription.name", "MD-NEXT"),
        status=status, active=limits["access_allowed"], blocked_message=blocked_message, expiry=expiry,
        days=days, usage=size(usage), total=size(total) if total else "Без ограничений",
        limited=bool(total), remaining=size(max(0, total - usage)) if total else "Без ограничений",
        percent=max(0, min(100, round(usage / total * 100))) if total else 0,
        monthly=bool(client.monthly_traffic_limit), reset=limits["traffic_period_end"],
        cdn_available=cdn_available, cdn_usage=size(limits['cdn_monthly_traffic_used']),
        cdn_total=size(limits['cdn_monthly_traffic_limit']) if limits['cdn_monthly_traffic_limit'] else 'Без ограничений',
        cdn_remaining=size(max(0, limits['cdn_monthly_traffic_limit']-limits['cdn_monthly_traffic_used'])),
        cdn_limited=bool(limits['cdn_monthly_traffic_limit']), cdn_exhausted=limits['cdn_quota_exhausted'],
        cdn_percent=max(0, min(100, round(limits['cdn_monthly_traffic_used'] / limits['cdn_monthly_traffic_limit'] * 100))) if limits['cdn_monthly_traffic_limit'] else 0,
        telegram=telegram, telegram_qr=telegram_qr,
        vpn_available=bool(vpn_connections), connections=connections,
        listed_connections=[c for c in connections if c["kind"] != "mtproto"],
        recommended_profile=vpn_connections[0]["name"] if vpn_connections else "",
        awg_available=any(c["kind"] == "awg" for c in connections),
        url=url, happ="happ://add/" + url,
        v2ray="v2rayng://install-sub?url=" + quote(url, safe=""),
        qr="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode())
