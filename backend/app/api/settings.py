from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
import asyncio
from app.services import placeholder
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.models.setting import Setting
from app.api.auth import get_current_user
from app.bot.bot import bot_manager
from app.services.telegram_settings import (
    get_telegram_settings_from_db,
    mask_token, resolve_telegram_proxy
)
from app.services.crypto import encrypt_secret
from app.services.events import log_event

router = APIRouter(prefix="/api/v1/settings", tags=["Настройки"], dependencies=[Depends(get_current_user)])


@router.get('/placeholder')
async def get_placeholder():
    return await asyncio.to_thread(placeholder.status)


@router.get('/placeholder/content', response_class=PlainTextResponse)
async def get_placeholder_content():
    try:
        return PlainTextResponse(await asyncio.to_thread(placeholder.content), headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})
    except FileNotFoundError:
        raise HTTPException(404, 'Страница ещё не создана')


@router.put('/placeholder')
async def upload_placeholder(request: Request, filename: str = 'index.html'):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > placeholder.MAX_BYTES:
            raise HTTPException(413, 'HTML-файл превышает 1 МБ')
        data.extend(chunk)
    try:
        result = await asyncio.to_thread(placeholder.replace, bytes(data), filename)
        log_event('info', 'settings', 'Загружен свой сайт-заглушка')
        return result
    except ValueError as error:
        raise HTTPException(400, str(error))


@router.post('/placeholder/generate')
async def generate_placeholder():
    result = await asyncio.to_thread(placeholder.replace, placeholder.generate(), 'index.html', 'generated')
    log_event('info', 'settings', 'Создано новое оформление сайта-заглушки')
    return result


@router.post('/placeholder/restore')
async def restore_placeholder():
    try:
        result = await asyncio.to_thread(placeholder.restore)
        log_event('info', 'settings', 'Восстановлен предыдущий сайт-заглушка')
        return result
    except ValueError as error:
        raise HTTPException(400, str(error))

class SubscriptionSettings(BaseModel):
    name: str = Field(default="MD-NEXT", min_length=1, max_length=25)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Введите название без управляющих символов")
        return value


@router.get("/subscription", response_model=SubscriptionSettings)
async def get_subscription_settings(db: AsyncSession = Depends(get_db)):
    row = await db.get(Setting, "subscription.name")
    return SubscriptionSettings(name=row.value if row else "MD-NEXT")


@router.put("/subscription", response_model=SubscriptionSettings)
async def update_subscription_settings(req: SubscriptionSettings, db: AsyncSession = Depends(get_db)):
    row = await db.get(Setting, "subscription.name")
    if row:
        row.value = req.name
    else:
        db.add(Setting(key="subscription.name", value=req.name))
    await db.commit()
    return req


class TelegramSettingsRequest(BaseModel):
    token: Optional[str] = None
    admin_id: Optional[str] = None
    proxy_url: Optional[str] = None
    notify_node_down: Optional[bool] = None
    notify_failover: Optional[bool] = None
    notify_quota: Optional[bool] = None
    use_node: Optional[bool] = None
    node_id: Optional[int] = Field(default=None, gt=0)

class TelegramSettingsResponse(BaseModel):
    token_set: bool
    token_masked: str
    admin_id: str
    proxy_url: str
    notify_node_down: bool
    notify_failover: bool
    notify_quota: bool
    bot_status: str
    use_node: bool
    node_id: Optional[int]
    bot_error: Optional[str] = None

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
        bot_status=bot_manager.status,
        use_node=tg_settings["use_node"],
        node_id=tg_settings["node_id"],
        bot_error=bot_manager.last_error,
    )

@router.put("/telegram", response_model=TelegramSettingsResponse)
async def update_telegram_settings(req: TelegramSettingsRequest, db: AsyncSession = Depends(get_db)):
    tg_settings = await get_telegram_settings_from_db()
    existing_token = tg_settings["token"]

    token_to_use = req.token.strip() if req.token is not None and req.token.strip() else existing_token
    effective = dict(tg_settings)
    for key in ("proxy_url", "use_node", "node_id"):
        if key in req.model_fields_set and (key == "node_id" or getattr(req, key) is not None):
            effective[key] = getattr(req, key)
    if req.admin_id is not None and req.admin_id.strip():
        if not req.admin_id.strip().isdigit() or int(req.admin_id.strip()) <= 0:
            raise HTTPException(status_code=400, detail="ID администратора должен быть положительным числом")
    try:
        proxy_to_use = await resolve_telegram_proxy(db, effective)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if token_to_use:
        try:
            await bot_manager.validate_token(token_to_use, proxy_to_use)
        except Exception:
            raise HTTPException(status_code=400, detail="Не удалось проверить токен через выбранный выход. Проверьте токен и доступность Telegram; настройки сохранены без изменений.")

    if req.token is not None and req.token.strip():
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

    for key in ("use_node", "node_id"):
        if key in req.model_fields_set and (key == "node_id" or req.use_node is not None):
            setting = await db.get(Setting, "telegram_" + key)
            value = ("true" if req.use_node else "false") if key == "use_node" else str(req.node_id or "")
            if setting: setting.value = value
            else: db.add(Setting(key="telegram_" + key, value=value))
    await db.commit()
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
        from app.bot.notification_cards import test_notice
        notice = test_notice()
        await bot.send_message(chat_id=admin_id, text=notice.text, parse_mode="HTML", reply_markup=notice.markup(), disable_notification=True)
        return {"status": "ok", "message": "Тестовое сообщение отправлено"}
    except Exception:
        raise HTTPException(status_code=502, detail="Не удалось отправить сообщение. Проверьте выход бота и начните диалог с ним командой /start.")
