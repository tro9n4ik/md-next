"""Личная страница без доступа к административным API и данным других клиентов."""
import base64
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import qrcode
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.services.client_limits import limit_info, utc
from app.services.cdn import make_cdn_link
from app.services.profiles import PROFILE_LABELS

environment = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
                          autoescape=select_autoescape(["html"]))


def size(value):
    value = max(0, value or 0)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if value < 1024 or unit == "ТиБ":
            return f"{value:.1f} {unit}"
        value /= 1024


def render_subscription_page(client, profiles, settings, enabled, url, usage, total):
    limits = limit_info(client)
    reasons = {"disabled": "Приостановлена", "expired": "Срок истёк", "monthly_quota": "Лимит исчерпан"}
    status = reasons.get(limits["blocked_reason"], "Активна")
    names = [PROFILE_LABELS[p.kind] for p in profiles if p.is_enabled and p.kind in enabled]
    if any(p.kind in enabled and make_cdn_link(client, p, settings) for p in profiles):
        names.append("Обход БС · CDN")
    buffer = BytesIO()
    qrcode.make(url).save(buffer, format="PNG")
    expiry = utc(client.expires_at) if client.expires_at else None
    days = max(0, int((expiry - datetime.now(timezone.utc)).total_seconds() // 86400)) if expiry else None
    return environment.get_template("subscription.html").render(
        name=client.name, status=status, active=limits["access_allowed"], expiry=expiry,
        days=days, usage=size(usage), total=size(total) if total else "Без ограничений",
        percent=min(100, round(usage / total * 100)) if total else 0,
        monthly=bool(client.monthly_traffic_limit), reset=limits["traffic_period_end"],
        profiles=names, url=url, happ="happ://add/" + url,
        v2ray="v2rayng://install-sub?url=" + quote(url, safe=""),
        qr="data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode())
