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
binding_inputs={}


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
    result=await handler(event,data)
    from aiogram.types import Message
    if isinstance(event,Message):
        from .chat_screen import clean_input
        await clean_input(event)
    return result


async def own_client(db,actor):
    row=(await db.execute(select(TelegramLink).where(TelegramLink.telegram_id==str(actor)))).scalar_one_or_none()
    return await db.get(Client,row.client_id) if row else None


async def show_account(message,actor,edit=False):
    from .handlers import keyboard,show_screen
    async with AsyncSessionLocal() as db:
        client=await own_client(db,actor)
        if not client:
            await show_screen(message,'<b>Добро пожаловать в MD-Next</b>\n\nЗдесь будет ваша подписка: подключение, срок и остаток трафика.\n\nПолучите одноразовый код у администратора и нажмите кнопку ниже.',keyboard([('🔑 Привязать подписку','mine:bind')],[('❔ Как это работает','mine:help')]),edit=edit);return
        info=limit_info(client)
        date=utc(client.expires_at).strftime('%d.%m.%Y %H:%M UTC') if client.expires_at else 'Без срока'
        status='Доступна' if info['access_allowed'] else {'disabled':'Приостановлена','expired':'Срок истёк','monthly_quota':'Лимит исчерпан'}.get(info['blocked_reason'],'Недоступна')
        quota=f"{info['monthly_traffic_limit']/1024**3:.2f} ГБ" if info['monthly_traffic_limit'] else 'без лимита'
        cdn_quota=f"{info['cdn_monthly_traffic_limit']/1024**3:g} ГБ" if info['cdn_monthly_traffic_limit'] else 'без лимита'
        text=(f"<b>Моя подписка · {escape(client.name)}</b>\n\n<b>{status}</b>\nСрок: {date}\n\n"
              f"Трафик за месяц: <b>{info['monthly_traffic_used']/1024**3:.2f} ГБ / {quota}</b>\n"
              f"Обход БС: <b>{info['cdn_monthly_traffic_used']/1024**3:.2f} ГБ / {cdn_quota}</b>"+(' · лимит исчерпан' if info['cdn_quota_exhausted'] else '')+'\n'+
              f"Новый расчётный период: {utc(info['traffic_period_end']).strftime('%d.%m.%Y')}\n\n"
              f"{'Откройте подключение, чтобы добавить подписку в VPN-приложение.' if info['access_allowed'] else 'Для восстановления доступа обратитесь к администратору.'}")
    rows=[[('📲 Подключиться','mine:access')],[('🛡 Файл AmneziaWG','mine:awg')]] if info['access_allowed'] else [[('Что делать с ограничением?','mine:help:problem')]]
    rows.append([('📖 Инструкции','mine:help'),('🔄 Обновить','mine:home')])
    await show_screen(message,text,keyboard(*rows),edit=edit)


@router.message(CommandStart())
@router.message(Command('me','menu','status'))
async def account(message):
    binding_inputs.pop((message.from_user.id,message.chat.id),None)
    await show_account(message,message.from_user.id)


@router.message(Command('bind'))
async def bind(message):
    from .handlers import show_screen,keyboard
    async def error(text):
        await show_screen(message,text,keyboard([('🔑 Ввести код снова','mine:bind')],[('← Моя подписка','mine:home')]))
    now=time.monotonic()
    for actor,v in list(attempts.items()):
        if now-v[0]>600: attempts.pop(actor,None)
    actor=message.from_user.id
    start,count=attempts.get(actor,(now,0))
    if count>=5 or len(attempts)>10000:
        await error('Слишком много попыток. Повторите через 10 минут.');return
    attempts[actor]=(start,count+1)
    parts=(message.text or '').split(maxsplit=1)
    code=parts[1].strip() if len(parts)>1 else ''
    digest=hashlib.sha256(code.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        row=(await db.execute(select(TelegramLink).where(TelegramLink.code_hash==digest))).scalar_one_or_none()
        if not row or not row.expires_at or utc(row.expires_at)<=datetime.now(timezone.utc):
            await error('Код неверен или истёк. Попросите новый у администратора.');return
        existing=(await db.execute(select(TelegramLink).where(TelegramLink.telegram_id==str(actor),TelegramLink.client_id!=row.client_id))).scalar_one_or_none()
        if existing:
            await error('У вас уже привязана другая подписка. Обратитесь к администратору.');return
        result=await db.execute(update(TelegramLink).where(TelegramLink.client_id==row.client_id,TelegramLink.code_hash==digest).values(telegram_id=str(actor),code_hash=None,expires_at=None))
        if result.rowcount!=1:
            await db.rollback();await error('Код уже использован.');return
        await db.commit()
    attempts.pop(actor,None)
    binding_inputs.pop((actor,message.chat.id),None)
    await show_account(message,actor)


@router.callback_query(F.data.startswith('mine:'))
async def mine(callback):
    from .handlers import show_screen,keyboard
    await callback.answer()
    actor=callback.from_user.id
    key=(actor,callback.message.chat.id)
    binding_inputs.pop(key,None)
    if callback.data=='mine:bind':
        for old,expiry in list(binding_inputs.items()):
            if expiry<time.monotonic():binding_inputs.pop(old,None)
        binding_inputs[key]=time.monotonic()+300
        await show_screen(callback.message,'<b>Привязка подписки</b>\n\nОтправьте одноразовый код, полученный у администратора. Это не пароль от сервера и не ссылка подписки.\n\nВвод можно отменить кнопкой ниже.',keyboard([('Отмена','mine:home')]),edit=True);return
    if callback.data.startswith('mine:help'):
        topic=callback.data.removeprefix('mine:help').lstrip(':')
        guides={
            'happ': '1. Откройте «Подключиться» и личную страницу.\n2. Нажмите «Добавить в Happ» и подтвердите импорт.\n3. Обновите подписку, выберите профиль и включите VPN.\n4. Разрешите VPN-подключение при первом запуске.',
            'v2rayng': '1. Скопируйте личную ссылку из «Подключиться».\n2. Добавьте её как подписку в v2rayNG.\n3. Обновите список серверов и выберите профиль.\n4. Включите VPN. Для XHTTP нужна поддерживающая его версия приложения.',
            'awg': '1. В «Подключиться» запросите файл AmneziaWG.\n2. Сохраните файл .conf и импортируйте в приложение AmneziaWG.\n3. Включите туннель. Этот файл не импортируется как обычная подписка Happ.',
            'problem': 'Проверьте срок и трафик на главном экране. Обновите подписку в приложении. Если проблема остаётся, сообщите администратору приложение и его версию, профиль, время и текст ошибки. Пароль и личную ссылку публиковать не нужно.',
        }
        if topic in guides:
            await show_screen(callback.message,'<b>📖 Инструкция</b>\n\n'+guides[topic],keyboard([('← Все инструкции','mine:help')],[('🏠 Моя подписка','mine:home')]),edit=True)
        else:
            await show_screen(callback.message,'<b>Чем помочь?</b>\n\nВыберите ваше приложение или описание проблемы. Для доступа нужна подписка, выданная администратором.',keyboard([('Happ','mine:help:happ'),('v2rayNG','mine:help:v2rayng')],[('AmneziaWG','mine:help:awg')],[('Не получается подключиться','mine:help:problem')],[('← Моя подписка','mine:home')]),edit=True)
        return
    if callback.data in ('mine:access','mine:awg'):
        async with AsyncSessionLocal() as db:
            client=await own_client(db,actor)
            if not client or not limit_info(client)['access_allowed']:
                await show_screen(callback.message,'Подписка недоступна. Проверьте срок и трафик в личном кабинете или обратитесь к администратору.',keyboard([('← Моя подписка','mine:home')]),edit=True);return
            client_id=client.id
        from .subscriptions import issue_access
        await issue_access(callback.message,client_id,awg=callback.data=='mine:awg',return_to='mine:home')
        return
    await show_account(callback.message,actor,edit=True)


@router.message(F.text)
async def text_entry(message):
    key=(message.from_user.id,message.chat.id)
    expiry=binding_inputs.pop(key,0)
    if expiry>time.monotonic() and not message.text.startswith('/'):
        return await bind(message.model_copy(update={'text':'/bind '+message.text.strip()}))
    return await show_account(message,message.from_user.id)
