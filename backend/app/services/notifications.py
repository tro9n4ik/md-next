"""Persistent subscription reminders: one delivery per expiry/period/threshold."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.models.setting import Setting
from app.services.client_limits import limit_info,utc
from app.services.telegram_settings import get_telegram_settings_from_db
from app.bot.notification_cards import card, safe
from app.services.telegram_delivery import send_notice, deliver_pending

logger=logging.getLogger(__name__)


def reminder_candidates(client,now):
    if not client.is_active: return []
    info=limit_info(client,now)
    notices=[]
    if client.expires_at:
        remaining=(utc(client.expires_at)-now).total_seconds()
        threshold='expired' if remaining<=0 else '1d' if remaining<=86400 else '3d' if remaining<=259200 else None
        if threshold:
            suffix=f"expiry:{utc(client.expires_at).isoformat()}:{threshold}"
            text='срок подписки истёк' if remaining<=0 else 'срок подписки заканчивается '+('в течение суток' if threshold=='1d' else 'в течение трёх дней')
            notices.append((suffix,text))
    limit=info['monthly_traffic_limit']
    used=info['monthly_traffic_used']
    if limit and used>=limit*.8:
        threshold=100 if used>=limit else 80
        notices.append((f"quota:{info['traffic_period_start'].isoformat()}:{threshold}",f"израсходовано {threshold}% месячного лимита ({used/1024**3:.2f} / {limit/1024**3:.2f} ГБ)"))
    return notices


def reminder_line(client, suffix, now):
    kind, threshold = suffix.split(':')[0], suffix.rsplit(':', 1)[-1]
    if kind == 'expiry':
        date = utc(client.expires_at).strftime('%d.%m.%Y · %H:%M UTC')
        label = {'expired': 'Срок истёк', '1d': 'Осталось меньше суток', '3d': 'Осталось не больше трёх дней'}[threshold]
        return f'{label} · {date}'
    info = limit_info(client, now)
    used, limit = info['monthly_traffic_used'], info['monthly_traffic_limit']
    percent = int(used * 100 / limit)
    label = 'Лимит исчерпан' if threshold == '100' else f'Использовано {percent}% лимита'
    reset = info['traffic_period_end'].strftime('%d.%m.%Y')
    return f'{label} · {used / 1024**3:.2f} / {limit / 1024**3:.2f} ГБ. Новый период: {reset} UTC'


async def send_reminders():
    from app.bot.bot import get_bot
    bot=get_bot()
    settings=await get_telegram_settings_from_db()
    if not bot or not settings.get('admin_id') or not settings.get('notify_quota'): return
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        clients=(await db.execute(select(Client).order_by(Client.id))).scalars().all()
        entries=[]
        for client in clients:
            pending=[]
            for suffix,message in reminder_candidates(client,now):
                # Fixed-size keys; replacing a threshold key avoids unbounded accumulation.
                kind=suffix.split(':')[0]+':'+suffix.rsplit(':',1)[-1]
                key=f'notify.client.{client.id}.{kind}'
                row=await db.get(Setting,key)
                if row and row.value==suffix: continue
                pending.append((key,suffix))
            if pending:
                text=f'<b>{safe(client.name, 80)}</b> · #{client.id}\n'+ '\n'.join('• '+safe(reminder_line(client,suffix,now),260) for _,suffix in pending)
                critical=any(suffix.endswith(':expired') or suffix.endswith(':100') for _,suffix in pending)
                entries.append((client.id,text,pending,critical))

        # Split on whole clients, leaving room for the title, hints and footer.
        batches=[]
        batch=[]
        length=0
        for entry in entries:
            if batch and (length+len(entry[1])>3000 or len(batch)>=10):
                batches.append(batch);batch=[];length=0
            batch.append(entry);length+=len(entry[1])+2
        if batch:batches.append(batch)
        for batch in batches:
            one=len(batch)==1
            notice=card('📋','Подписки требуют внимания',['\n\n'.join(entry[1] for entry in batch)],
                hint='Откройте карточку подписки, чтобы проверить срок или изменить лимит.',
                action=f'ops:detail:{batch[0][0]}' if one else 'ops:list:0',
                button='Открыть подписку' if one else 'Открыть подписки',silent=not any(entry[3] for entry in batch),now=now)
            if not await send_notice(bot,settings['admin_id'],notice):break
            for _,_,pending,_ in batch:
                for key,suffix in pending:
                    row=await db.get(Setting,key)
                    if row:row.value=suffix
                    else:db.add(Setting(key=key,value=suffix))
                    # A delivered higher threshold also covers lower warnings.
                    thresholds = ['80'] if suffix.endswith(':100') else ['1d','3d'] if suffix.endswith(':expired') else ['3d'] if suffix.endswith(':1d') else []
                    for lower in thresholds:
                        lower_key=key.rsplit(':',1)[0]+':'+lower
                        lower_suffix=suffix.rsplit(':',1)[0]+':'+lower
                        lower_row=await db.get(Setting,lower_key)
                        if lower_row:lower_row.value=lower_suffix
                        else:db.add(Setting(key=lower_key,value=lower_suffix))
                await db.commit()
            if batch is not batches[-1]:await asyncio.sleep(1.1)


async def notification_loop():
    next_reminders=0.0
    while True:
        try:
            await deliver_pending()
            if time.monotonic()>=next_reminders:
                await send_reminders()
                next_reminders=time.monotonic()+60
        except asyncio.CancelledError: raise
        except Exception: logger.exception('Ошибка проверки напоминаний')
        await asyncio.sleep(5)
