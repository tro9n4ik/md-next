"""Admin search, confirmed subscription actions, templates and diagnostics."""
import json
import secrets
import time
from datetime import datetime,timedelta,timezone
from html import escape
from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.services.client_limits import limit_info,utc

pending={}
inputs={}
filters={}


def fingerprint(client):
    return (bool(client.is_active),str(client.expires_at),int(client.monthly_traffic_limit or 0))


def clean():
    for mapping in (pending,inputs,filters):
        for key,value in list(mapping.items()):
            if value['expires']<time.monotonic(): mapping.pop(key,None)


async def client_list(message,actor,page=0):
    from .handlers import keyboard,show_screen
    clean();state=filters.get(actor,{'q':'','filter':'all'})
    async with AsyncSessionLocal() as db:
        clients=(await db.execute(select(Client).order_by(Client.id.desc()))).scalars().all()
    now=datetime.now(timezone.utc);q=state.get('q','').casefold();kind=state.get('filter','all')
    clients=[c for c in clients if q in ' '.join([c.name,c.phone or '',c.email or '']).casefold()]
    if kind=='soon': clients=[c for c in clients if c.expires_at and now<utc(c.expires_at)<=now+timedelta(days=7)]
    elif kind=='quota': clients=[c for c in clients if limit_info(c)['blocked_reason']=='monthly_quota']
    elif kind=='paused': clients=[c for c in clients if not c.is_active]
    page=max(0,min(page,max(0,(len(clients)-1)//8)))
    rows=[[('🔎 Поиск','ops:search'),('Все','ops:filter:all')],[('⌛ Скоро истекают','ops:filter:soon')],[('📦 Лимит исчерпан','ops:filter:quota'),('⏸ Приостановлены','ops:filter:paused')]]
    for c in clients[page*8:page*8+8]: rows.append([(('🟢 ' if limit_info(c)['access_allowed'] else '🔴 ')+c.name[:35],f'ops:detail:{c.id}')])
    nav=[]
    if page: nav.append(('← Назад',f'ops:list:{page-1}'))
    if (page+1)*8<len(clients): nav.append(('Далее →',f'ops:list:{page+1}'))
    if nav: rows.append(nav)
    rows.append([('🏠 Главное меню','md:home')])
    label={'all':'Все','soon':'Скоро истекают','quota':'Лимит исчерпан','paused':'Приостановлены'}.get(kind,'Все')
    await show_screen(message,f'<b>Подписки · найдено {len(clients)}</b>\nПоиск: {escape(q) or "—"} · Фильтр: {label}',keyboard(*rows),edit=True)


async def detail(message,client_id):
    from .subscriptions import detail as existing
    from .handlers import keyboard
    await existing(message,client_id)
    await message.edit_reply_markup(reply_markup=keyboard(
        [('🔗 Ссылка и QR',f'sub:link:{client_id}'),('🛡 AWG',f'sub:awg:{client_id}')],
        [('📅 +30 дней',f'ops:extend:{client_id}'),('📅 Своя дата',f'ops:date:{client_id}')],
        [('📦 Лимит',f'ops:quota:{client_id}'),('🔄 Сброс расхода',f'ops:reset:{client_id}')],
        [('⏯ Вкл./выкл.',f'ops:toggle:{client_id}'),('🔐 Профили',f'ops:profiles:{client_id}')],
        [('👤 Код Telegram',f'ops:bind:{client_id}'),('Отозвать привязку',f'ops:unlink:{client_id}')],
        [('← Подписки','ops:list:0'),('🏠 Меню','md:home')]))


async def prepare(message,actor,client_id,payload,label):
    from .handlers import keyboard,show_screen
    clean()
    async with AsyncSessionLocal() as db:
        client=await db.get(Client,client_id)
        if not client: raise ValueError('Клиент не найден')
    for token,v in list(pending.items()):
        if (v['chat'],v['message'])==(message.chat.id,message.message_id): pending.pop(token,None)
    token=secrets.token_hex(8)
    pending[token]={'actor':actor,'chat':message.chat.id,'message':message.message_id,'id':client_id,'payload':payload,'state':fingerprint(client),'expires':time.monotonic()+120}
    await show_screen(message,f'<b>{escape(client.name)}</b>\n\n{escape(label)}\nПодтвердите изменение.',keyboard([('✅ Применить','ops:confirm:'+token)],[('Отмена',f'ops:detail:{client_id}')]),edit=True)


async def diagnosis(message):
    from app.services.diagnostics import run_diagnostics
    from .handlers import back_menu
    await message.answer('Проверяю сервер. Это может занять около минуты.')
    async with AsyncSessionLocal() as db: report=await run_diagnostics(db)
    lines=[('✅' if c['ok'] else '⚪' if c['ok'] is None else '❌')+' '+c['name']+': '+c['detail'] for c in report['checks']]
    await message.answer('\n'.join(lines)+'\n\n'+report['notice'],parse_mode=None,reply_markup=back_menu())
    await message.answer_document(BufferedInputFile(json.dumps(report,ensure_ascii=False,indent=2).encode(),filename='diagnostics.json'))


async def callback(callback):
    from .handlers import keyboard,show_screen
    from . import subscriptions
    clean();action=callback.data.removeprefix('ops:');actor=callback.from_user.id
    await callback.answer()
    if not action.startswith('confirm:'):
        for token,v in list(pending.items()):
            if (v['chat'],v['message'])==(callback.message.chat.id,callback.message.message_id): pending.pop(token,None)
    inputs.pop((actor,callback.message.chat.id),None)
    subscriptions.drafts.pop((actor,callback.message.chat.id),None)
    try:
        if action=='diagnostics': await diagnosis(callback.message);return
        if action=='templates':
            from app.services.templates import get_templates
            async with AsyncSessionLocal() as db: templates=await get_templates(db)
            rows=[[(t.name,'ops:template:'+t.id)] for t in templates]
            await show_screen(callback.message,'<b>Создать по шаблону</b>\nШаблоны настраиваются в панели.',keyboard(*rows,[('🏠 Меню','md:home')]),edit=True);return
        if action.startswith('template:'):
            from app.services.templates import get_templates
            async with AsyncSessionLocal() as db: templates=await get_templates(db)
            chosen=next((t for t in templates if t.id==action.split(':')[1]),None)
            if not chosen: raise ValueError('Шаблон больше не существует')
            await subscriptions.start(callback.message,actor,edit=True)
            draft=subscriptions.get_draft(actor,callback.message.chat.id)
            draft.values.update(subscription_period=chosen.period,monthly_traffic_limit=chosen.monthly_traffic_limit,_template=True)
            return
        if action=='search':
            inputs[(actor,callback.message.chat.id)]={'kind':'search','expires':time.monotonic()+300}
            await callback.message.answer('Отправьте имя, телефон или почту. /cancel отменяет ввод.');return
        if action.startswith('filter:'):
            filters[actor]={'filter':action.split(':')[1],'q':'','expires':time.monotonic()+1800}
            await client_list(callback.message,actor);return
        if action.startswith('list:'): await client_list(callback.message,actor,int(action.split(':')[1]));return
        if action.startswith('confirm:'):
            token=action.split(':')[1];v=pending.get(token)
            if not v or (v['actor'],v['chat'],v['message'])!=(actor,callback.message.chat.id,callback.message.message_id): raise ValueError('Подтверждение устарело')
            v=pending.pop(token)
            async with AsyncSessionLocal() as db:
                client=await db.get(Client,v['id'])
                if not client or fingerprint(client)!=v['state']: raise ValueError('Условия уже изменились. Откройте карточку снова')
                payload=v['payload'];from app.api import operations,clients
                if payload.get('operation')=='reset': await operations.reset(client.id,db)
                elif payload.get('operation')=='extend': await operations.extend(client.id,db)
                elif payload.get('operation')=='unlink': await operations.unlink(client.id,db)
                elif 'profiles' in payload: await clients.update_client_access(client.id,clients.ClientAccessUpdate(**payload),db)
                else: await clients.update_client(client.id,clients.ClientUpdate(**payload),db)
            await callback.message.answer('Изменения применены.');await detail(callback.message,v['id']);return
        command,value=action.split(':',1);parts=value.split(':');client_id=int(parts[0])
        if command=='detail': await detail(callback.message,client_id);return
        if command in ('quota','date'):
            inputs[(actor,callback.message.chat.id)]={'kind':command,'id':client_id,'expires':time.monotonic()+300}
            await callback.message.answer('Отправьте лимит в ГБ (0 = без лимита).' if command=='quota' else 'Отправьте будущую дату ДД.ММ.ГГГГ, до конца дня UTC.');return
        if command=='profiles':
            from app.api.clients import get_client_profiles
            async with AsyncSessionLocal() as db: data=await get_client_profiles(client_id,db)
            rows=[[(('✅ ' if p['is_enabled'] else '⛔ ')+p['label'],f"ops:profile:{client_id}:{p['kind']}:{int(not p['is_enabled'])}")] for p in data['access'] if p['available']]
            await show_screen(callback.message,'<b>Профили доступа</b>\nВыберите профиль для изменения.',keyboard(*rows,[("Назад",f'ops:detail:{client_id}')]),edit=True);return
        if command=='profile': await prepare(callback.message,actor,client_id,{'profiles':{parts[1]:bool(int(parts[2]))}},'Изменить доступ к профилю '+parts[1]);return
        if command=='bind':
            from app.api.operations import telegram_code
            async with AsyncSessionLocal() as db: data=await telegram_code(client_id,db)
            await callback.message.answer('Код действует 15 минут. Передайте владельцу подписки:\n'+data['instruction'],parse_mode=None);return
        if command=='toggle':
            async with AsyncSessionLocal() as db: c=await db.get(Client,client_id)
            if not c: raise ValueError('Клиент не найден')
            await prepare(callback.message,actor,client_id,{'is_active':not c.is_active},'Включить доступ' if not c.is_active else 'Приостановить доступ');return
        if command in ('extend','reset','unlink'):
            label={'extend':'Продлить на 30 дней. Для бессрочной подписки будет установлен срок от сегодняшней даты.','reset':'Обнулить расход месяца. Дата обновления периода сохраняется.','unlink':'Отозвать привязку Telegram и одноразовый код.'}[command]
            await prepare(callback.message,actor,client_id,{'operation':command},label)
    except Exception as exc:
        from fastapi import HTTPException
        text=exc.detail if isinstance(exc,HTTPException) and isinstance(exc.detail,str) else str(exc) if isinstance(exc,ValueError) else 'Не удалось выполнить действие. Откройте карточку и повторите.'
        await callback.message.answer(text,parse_mode=None)


async def text_input(message):
    clean();v=inputs.pop((message.from_user.id,message.chat.id),None)
    if not v:return
    if v['kind']=='search':
        filters[message.from_user.id]={'q':message.text[:100],'filter':'all','expires':time.monotonic()+1800}
        screen=await message.answer('Результаты поиска');await client_list(screen,message.from_user.id);return
    try:
        if v['kind']=='quota':
            from decimal import Decimal
            number=Decimal(message.text.replace(',','.'))
            if not number.is_finite() or number<0 or number>8388607: raise ValueError('Неверный лимит')
            payload={'monthly_traffic_limit':int(number*1024**3)};label=f'Установить лимит: {number} ГБ (0 = без лимита)'
        else:
            date=datetime.strptime(message.text.strip(),'%d.%m.%Y').replace(hour=23,minute=59,second=59,tzinfo=timezone.utc)
            if date<=datetime.now(timezone.utc):raise ValueError('Дата должна быть в будущем')
            payload={'expires_at':date.isoformat()};label='Окончание: '+date.strftime('%d.%m.%Y %H:%M UTC')
        screen=await message.answer('Подтвердите изменение')
        await prepare(screen,message.from_user.id,v['id'],payload,label)
    except Exception:
        await message.answer('Некорректное значение. Откройте карточку и повторите ввод.')


async def cancel(message):
    inputs.pop((message.from_user.id,message.chat.id),None)
    from .handlers import back_menu
    await message.answer('Ввод отменён.',reply_markup=back_menu())


def register(router):
    router.callback_query.register(callback,F.data.startswith('ops:'))
    router.message.register(cancel,Command('cancel'))
    router.message.register(diagnosis,Command('diagnostics'))
    router.message.register(text_input,F.text & ~F.text.startswith('/') & F.func(lambda m:(m.from_user.id,m.chat.id) in inputs))
