"""Последние уведомления в меню вместо отдельных сообщений в переписке."""
import asyncio
import json
from collections import defaultdict
import re
import secrets
import time

from app.db.database import AsyncSessionLocal
from app.models.setting import Setting

locks=defaultdict(asyncio.Lock)


def key(bot, chat):
    identifier=getattr(bot,'id',0)
    return f'telegram.inbox.{identifier if isinstance(identifier,int) else 0}.{chat}'


async def read(bot,chat):
    async with AsyncSessionLocal() as db:
        row=await db.get(Setting,key(bot,chat))
        try:
            items=json.loads(row.value) if row else []
            return [item for item in items if isinstance(item,dict) and all(isinstance(item.get(field),str) for field in ('id','text','action','button','channel'))] if isinstance(items,list) else []
        except (ValueError,TypeError):
            return []


async def save(bot,chat,notice,channel):
    async with locks[key(bot,chat)]:
        return await _save(bot,chat,notice,channel)


async def _save(bot,chat,notice,channel):
    items=await read(bot,chat)
    previous=next((item for item in items if item.get('channel')==channel),None)
    value={'id':previous['id'] if previous else secrets.token_hex(4),'channel':channel,
           'text':notice.text,'action':notice.action,'button':notice.button,'at':time.time()}
    items=[value]+[item for item in items if item.get('channel')!=channel]
    async with AsyncSessionLocal() as db:
        identifier=key(bot,chat);row=await db.get(Setting,identifier)
        data=json.dumps(items[:20],ensure_ascii=False)
        if row:row.value=data
        else:db.add(Setting(key=identifier,value=data))
        await db.commit()


def editing(chat):
    from . import subscriptions,admin_tools,handlers
    admin_tools.clean()
    return bool(subscriptions.get_draft(chat,chat) or (chat,chat) in admin_tools.inputs
                or any(value['chat']==chat for value in admin_tools.pending.values())
                or any(value.chat_id==chat and value.expires_at>time.monotonic() for value in handlers.pending_actions.values())
                or (chat,chat) in admin_tools.diagnostic_running)


async def screen(message,event=None):
    from .handlers import show_screen,keyboard
    try:bot=message.bot
    except (RuntimeError,AttributeError):bot=None
    items=await read(bot,message.chat.id)
    chosen=next((item for item in items if item.get('id')==event),None)
    if chosen:
        await show_screen(message,chosen['text'],keyboard([(chosen['button'],chosen['action'])],
                          [('← Все события','md:inbox')],[('🏠 Главное меню','md:home')]),edit=True)
        return
    rows=[]
    for item in items:
        title=re.sub('<[^>]*>','',item['text']).split('\n')[0][:42]
        rows.append([(title,'md:inbox:'+item['id'])])
    rows.append([('🏠 Главное меню','md:home')])
    await show_screen(message,'<b>🔔 События</b>\n\n'+('Последние уведомления. Повторные сообщения по одной теме объединяются; настройки уведомлений сохраняются в панели.' if items else 'Пока нет уведомлений. Их типы можно выбрать в настройках Telegram в панели.'),keyboard(*rows),edit=True)
