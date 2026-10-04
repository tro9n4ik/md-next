"""Сохранение черновика CDN. Включение откладывается до проверки ресурса провайдера."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.setting import Setting
from app.services.cdn import validate_domain
from app.services.profiles import get_profile_settings

router = APIRouter(prefix="/api/v1/cdn", tags=["CDN — подготовка"], dependencies=[Depends(get_current_user)])


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
    return {"enabled": False, "state": "draft", "domain": values.get("cdn.domain", ""),
            "origin_path": values["profiles.path.vless_xhttp_tls"], "mode": "packet-up",
            "message": "Каркас готов. Перед включением требуется проверить реальный CDN-ресурс и доступность из сети клиента."}


@router.put("")
async def save_draft(request: CdnDraft, db: AsyncSession = Depends(get_db)):
    if request.enabled:
        raise HTTPException(status_code=409, detail="CDN находится в подготовке. Сначала подключите ресурс провайдера и проверьте передачу XHTTP.")
    row = await db.get(Setting, "cdn.domain")
    if row:
        row.value = request.domain
    else:
        db.add(Setting(key="cdn.domain", value=request.domain))
    await db.commit()
    return await read_draft(db)
