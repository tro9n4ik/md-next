import ipaddress
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.setting import Setting

router = APIRouter(prefix="/api/v1/settings/dns", tags=["DNS"], dependencies=[Depends(get_current_user)])

DNS_DEFAULTS = {
    "dns.remote_type": "DoH",
    "dns.remote_domain": "https://cloudflare-dns.com/dns-query",
    "dns.remote_ip": "1.1.1.1",
    "dns.domestic_type": "DoU",
    "dns.domestic_domain": "",
    "dns.domestic_ip": "8.8.8.8",
    "dns.domain_strategy": "IPIfNonMatch",
    "dns.fake_dns": "false",
}


class DnsSettingsRequest(BaseModel):
    remote_type: Literal["DoH", "DoU"] = "DoH"
    remote_domain: str = Field(default="https://cloudflare-dns.com/dns-query", max_length=300)
    remote_ip: str = "1.1.1.1"
    domestic_type: Literal["DoH", "DoU"] = "DoU"
    domestic_domain: str = Field(default="", max_length=300)
    domestic_ip: str = "8.8.8.8"
    domain_strategy: Literal["AsIs", "IPIfNonMatch", "IPOnDemand"] = "IPIfNonMatch"
    fake_dns: bool = False


def _validate_ip(value: str, name: str) -> str:
    value = value.strip()
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name}: укажите корректный IP-адрес") from exc
    return value


def _validate_doh_url(value: str, name: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail=f"{name}: укажите адрес HTTPS-сервера DoH")
    return value


def _response(values: dict[str, str]) -> dict:
    return {
        "remote_type": values["dns.remote_type"],
        "remote_domain": values["dns.remote_domain"],
        "remote_ip": values["dns.remote_ip"],
        "domestic_type": values["dns.domestic_type"],
        "domestic_domain": values["dns.domestic_domain"],
        "domestic_ip": values["dns.domestic_ip"],
        "domain_strategy": values["dns.domain_strategy"],
        "fake_dns": values["dns.fake_dns"].lower() in {"true", "1", "yes"},
    }


@router.get("")
async def get_dns_settings(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(Setting.__table__.select().where(Setting.key.in_(DNS_DEFAULTS)))).all()
    values = dict(DNS_DEFAULTS)
    values.update({row.key: row.value for row in rows})
    return _response(values)


@router.put("")
async def update_dns_settings(request: DnsSettingsRequest, db: AsyncSession = Depends(get_db)):
    values = request.model_dump()
    values["remote_ip"] = _validate_ip(values["remote_ip"], "Удалённый DNS")
    values["domestic_ip"] = _validate_ip(values["domestic_ip"], "Локальный DNS")
    if values["remote_type"] == "DoH":
        values["remote_domain"] = _validate_doh_url(values["remote_domain"], "Удалённый DNS")
    if values["domestic_type"] == "DoH":
        values["domestic_domain"] = _validate_doh_url(values["domestic_domain"], "Локальный DNS")

    stored = {
        "dns.remote_type": values["remote_type"],
        "dns.remote_domain": values["remote_domain"].strip(),
        "dns.remote_ip": values["remote_ip"],
        "dns.domestic_type": values["domestic_type"],
        "dns.domestic_domain": values["domestic_domain"].strip(),
        "dns.domestic_ip": values["domestic_ip"],
        "dns.domain_strategy": values["domain_strategy"],
        "dns.fake_dns": str(values["fake_dns"]).lower(),
    }
    try:
        for key, value in stored.items():
            setting = await db.get(Setting, key)
            if setting is None:
                db.add(Setting(key=key, value=value))
            else:
                setting.value = value
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Не удалось сохранить настройки DNS")
    return _response(stored)
