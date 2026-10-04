"""Настройка CDN и включение дополнительного профиля после проверки транспорта."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.setting import Setting
from app.services.cdn import validate_domain, probe_cdn, cdn_path
from app.services.profiles import get_profile_settings, enabled_profile_kinds

router = APIRouter(prefix="/api/v1/cdn", tags=["CDN"], dependencies=[Depends(get_current_user)])


class CdnDraft(BaseModel):
    domain: str = Field(default="", max_length=253)
    enabled: bool = False

    @field_validator("domain")
    @classmethod
    def domain_is_valid(cls, value):
        return validate_domain(value)


@router.get("")
async def read_draft(db: AsyncSession = Depends(get_db)):
    values = await get_profile_settings(db)
    enabled = values.get("cdn.enabled", "false") == "true"
    return {"enabled": enabled, "state": "enabled" if enabled else "draft", "domain": values.get("cdn.domain", ""),
            "origin_path": cdn_path(values["profiles.path.vless_xhttp_tls"]), "mode": "packet-up · GET",
            "message": "После включения обновите подписку и проверьте профиль в Happ и в ограниченной сети."}


@router.post("/check")
async def check_cdn(request: CdnDraft, db: AsyncSession = Depends(get_db)):
    values = await get_profile_settings(db)
    return await probe_cdn(request.domain, values["profiles.path.vless_xhttp_tls"])


@router.put("")
async def save_draft(request: CdnDraft, db: AsyncSession = Depends(get_db)):
    if request.enabled:
        values = await get_profile_settings(db)
        checked = await probe_cdn(request.domain, values["profiles.path.vless_xhttp_tls"])
        if not checked["ok"]:
            raise HTTPException(status_code=409, detail=checked["message"])
        if "vless_xhttp_tls" not in await enabled_profile_kinds(db):
            raise HTTPException(status_code=409, detail="Сначала включите протокол XHTTP TLS во вкладке «Протоколы».")
    for key, value in {"cdn.domain": request.domain, "cdn.enabled": "true" if request.enabled else "false"}.items():
        row = await db.get(Setting, key)
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
    await db.commit()
    return await read_draft(db)
