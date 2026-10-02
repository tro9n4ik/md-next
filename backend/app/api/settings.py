from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.models.setting import Setting
from app.api.auth import get_current_user
from app.bot.bot import bot_manager
from app.services.telegram_settings import (
    get_telegram_settings_from_db,
    mask_token
)
from app.services.crypto import encrypt_secret
from app.services.events import log_event

router = APIRouter(prefix="/api/v1/settings", tags=["Настройки"], dependencies=[Depends(get_current_user)])

class TelegramSettingsRequest(BaseModel):
    token: Optional[str] = None
    admin_id: Optional[str] = None
    proxy_url: Optional[str] = None
    notify_node_down: Optional[bool] = None
    notify_failover: Optional[bool] = None
    notify_quota: Optional[bool] = None

class TelegramSettingsResponse(BaseModel):
    token_set: bool
    token_masked: str
    admin_id: str
    proxy_url: str
    notify_node_down: bool
    notify_failover: bool
    notify_quota: bool
    bot_status: str

@router.get("/telegram", response_model=TelegramSettingsResponse)
async def get_telegram_settings(db: AsyncSession = Depends(get_db)):
    tg_settings = await get_telegram_settings_from_db()
    token = tg_settings["token"]

    return TelegramSettingsResponse(
        token_set=bool(token),
        token_masked=mask_token(token),
        admin_id=str(tg_settings["admin_id"]) if tg_settings["admin_id"] != 0 else "",
        proxy_url=tg_settings["proxy_url"],
        notify_node_down=tg_settings["notify_node_down"],
        notify_failover=tg_settings["notify_failover"],
        notify_quota=tg_settings["notify_quota"],
        bot_status=bot_manager.status
    )

@router.put("/telegram", response_model=TelegramSettingsResponse)
async def update_telegram_settings(req: TelegramSettingsRequest, db: AsyncSession = Depends(get_db)):
    tg_settings = await get_telegram_settings_from_db()
    existing_token = tg_settings["token"]

    token_to_use = req.token.strip() if req.token is not None and req.token.strip() else existing_token

    if token_to_use:
        try:
            await bot_manager.validate_token(token_to_use, req.proxy_url)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Ошибка валидации токена Telegram через bot.getMe(): {e}")

    if req.token is not None:
        enc_value = encrypt_secret(req.token.strip())
        res_tok = await db.execute(select(Setting).where(Setting.key == "telegram_bot_token"))
        s_tok = res_tok.scalar_one_or_none()
        if s_tok:
            s_tok.value = enc_value
        else:
            db.add(Setting(key="telegram_bot_token", value=enc_value))

    if req.admin_id is not None:
        res_adm = await db.execute(select(Setting).where(Setting.key == "telegram_admin_id"))
        s_adm = res_adm.scalar_one_or_none()
        if s_adm:
            s_adm.value = req.admin_id.strip()
        else:
            db.add(Setting(key="telegram_admin_id", value=req.admin_id.strip()))

    if req.proxy_url is not None:
        res_prx = await db.execute(select(Setting).where(Setting.key == "telegram_proxy_url"))
        s_prx = res_prx.scalar_one_or_none()
        if s_prx:
            s_prx.value = req.proxy_url.strip()
        else:
            db.add(Setting(key="telegram_proxy_url", value=req.proxy_url.strip()))

    if req.notify_node_down is not None:
        res_nd = await db.execute(select(Setting).where(Setting.key == "telegram_notify_node_down"))
        s_nd = res_nd.scalar_one_or_none()
        val_str = "true" if req.notify_node_down else "false"
        if s_nd:
            s_nd.value = val_str
        else:
            db.add(Setting(key="telegram_notify_node_down", value=val_str))

    if req.notify_failover is not None:
        res_fo = await db.execute(select(Setting).where(Setting.key == "telegram_notify_failover"))
        s_fo = res_fo.scalar_one_or_none()
        val_str = "true" if req.notify_failover else "false"
        if s_fo:
            s_fo.value = val_str
        else:
            db.add(Setting(key="telegram_notify_failover", value=val_str))

    if req.notify_quota is not None:
        setting = await db.get(Setting, "telegram_notify_quota")
        value = "true" if req.notify_quota else "false"
        if setting:
            setting.value = value
        else:
            db.add(Setting(key="telegram_notify_quota", value=value))

    await db.commit()

    proxy_to_use = req.proxy_url.strip() if req.proxy_url is not None else tg_settings["proxy_url"]
    await bot_manager.reload(token_to_use, proxy_to_use)
    log_event("info", "telegram", "Настройки Telegram-бота изменены", {"token_configured": bool(token_to_use), "admin_configured": bool(req.admin_id or tg_settings["admin_id"])})

    return await get_telegram_settings(db)

@router.post("/telegram/test")
async def send_test_telegram_message(db: AsyncSession = Depends(get_db)):
    bot = bot_manager.bot
    if not bot or bot_manager.status != "running":
        raise HTTPException(status_code=400, detail="Telegram-бот не активен")

    tg_settings = await get_telegram_settings_from_db()
    admin_id = tg_settings["admin_id"]

    if admin_id == 0:
        raise HTTPException(status_code=400, detail="ID администратора Telegram не настроен")

    try:
        await bot.send_message(chat_id=admin_id, text="🟢 **Тестовое сообщение от MD-Next Панели**\nУведомления успешно настроены!", parse_mode="Markdown")
        return {"status": "ok", "message": "Тестовое сообщение отправлено"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось отправить тестовое сообщение: {e}")
