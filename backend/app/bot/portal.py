"""Client portal isolated from administrator commands."""
import hashlib
import time
from datetime import datetime, timezone
from html import escape
from aiogram import Router,F
from aiogram.filters import Command,CommandStart
from sqlalchemy import select,update
from app.db.database import AsyncSessionLocal
from app.models.operations import TelegramLink
from app.models.client import Client
from app.services.client_limits import limit_info,utc
from app.services.telegram_settings import get_telegram_settings_from_db

router=Router(name='client_portal')
attempts={}


async def non_admin(event):
    settings=await get_telegram_settings_from_db()
    return bool(event.from_user and event.from_user.id!=settings['admin_id'])


router.message.filter(non_admin)
router.callback_query.filter(non_admin)


@router.message.outer_middleware()
@router.callback_query.outer_middleware()
async def private_client(handler,event,data):
    message=event.message if hasattr(event,'message') else event
    if not event.from_user or not message or message.chat.type!='private':
        await event.answer('Откройте личный диалог с ботом.');return
    return await handler(event,data)


async def own_client(db,actor):
    row=(await db.execute(select(TelegramLink).where(TelegramLink.telegram_id==str(actor)))).scalar_one_or_none()
    return await db.get(Client,row.client_id) if row else None


async def show_account(message,actor,edit=False):
    from .handlers import keyboard,show_screen
    async with AsyncSessionLocal() as db:
        client=await own_client(db,actor)
        if not client:
            await message.answer('Получите код у администратора и отправьте /bind КОД.');return
        info=limit_info(client)
        date=utc(client.expires_at).strftime('%d.%m.%Y %H:%M UTC') if client.expires_at else 'Без срока'
        status='Доступна' if info['access_allowed'] else {'disabled':'Приостановлена','expired':'Срок истёк','monthly_quota':'Лимит исчерпан'}.get(info['blocked_reason'],'Недоступна')
        quota=f"{info['monthly_traffic_limit']/1024**3:.2f} ГБ" if info['monthly_traffic_limit'] else 'без лимита'
        text=f"<b>{escape(client.name)}</b>\n{status}\nДо: {date}\nТрафик: {info['monthly_traffic_used']/1024**3:.2f} ГБ / {quota}"
    await show_screen(message,text,keyboard([('🔗 Подключение','mine:access')],[('📖 Инструкция','mine:help'),('🔄 Обновить','mine:home')]),edit=edit)


@router.message(CommandStart())
@router.message(Command('me','menu','status'))
async def account(message):
    await show_account(message,message.from_user.id)


@router.message(Command('bind'))
async def bind(message):
    now=time.monotonic()
    for actor,v in list(attempts.items()):
        if now-v[0]>600: attempts.pop(actor,None)
    actor=message.from_user.id
    start,count=attempts.get(actor,(now,0))
    if count>=5 or len(attempts)>10000:
        await message.answer('Слишком много попыток. Повторите через 10 минут.');return
    attempts[actor]=(start,count+1)
    parts=(message.text or '').split(maxsplit=1)
    code=parts[1].strip() if len(parts)>1 else ''
    digest=hashlib.sha256(code.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        row=(await db.execute(select(TelegramLink).where(TelegramLink.code_hash==digest))).scalar_one_or_none()
        if not row or not row.expires_at or utc(row.expires_at)<=datetime.now(timezone.utc):
            await message.answer('Код неверен или истёк. Попросите новый у администратора.');return
        existing=(await db.execute(select(TelegramLink).where(TelegramLink.telegram_id==str(actor),TelegramLink.client_id!=row.client_id))).scalar_one_or_none()
        if existing:
            await message.answer('У вас уже привязана другая подписка. Обратитесь к администратору.');return
        result=await db.execute(update(TelegramLink).where(TelegramLink.client_id==row.client_id,TelegramLink.code_hash==digest).values(telegram_id=str(actor),code_hash=None,expires_at=None))
        if result.rowcount!=1:
            await db.rollback();await message.answer('Код уже использован.');return
        await db.commit()
    attempts.pop(actor,None)
    await message.answer('Подписка привязана. /me открывает личный кабинет.')
    await show_account(message,actor)


@router.callback_query(F.data.startswith('mine:'))
async def mine(callback):
    await callback.answer()
    actor=callback.from_user.id
    if callback.data=='mine:help':
        await callback.message.answer('Happ: добавьте ссылку подписки, обновите её, выберите профиль и включите VPN. AmneziaWG импортируется отдельным .conf файлом. При ошибке сообщите администратору время, профиль и текст ошибки.');return
    if callback.data=='mine:access':
        from app.api.clients import get_client_profiles
        async with AsyncSessionLocal() as db:
            client=await own_client(db,actor)
            if not client or not limit_info(client)['access_allowed']:
                await callback.message.answer('Подписка недоступна. Обратитесь к администратору.');return
            data=await get_client_profiles(client.id,db)
        from aiogram.types import BufferedInputFile
        if data['subscription_url']:
            import io,qrcode
            image=io.BytesIO();qrcode.make(data['subscription_url']).save(image,format='PNG')
            await callback.message.answer_photo(BufferedInputFile(image.getvalue(),filename='subscription.png'))
            await callback.message.answer('Ссылка подписки:\n'+data['subscription_url'],parse_mode=None,disable_web_page_preview=True)
        for profile in data['profiles']:
            if profile['kind']=='awg' and profile['is_enabled'] and profile['data']:
                await callback.message.answer_document(BufferedInputFile(profile['data'].encode(),filename='amneziawg.conf'))
        return
    await show_account(callback.message,actor,edit=True)
