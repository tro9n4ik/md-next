"""Persistent subscription reminders: one delivery per expiry/period/threshold."""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.models.setting import Setting
from app.services.client_limits import limit_info,utc
from app.services.telegram_settings import get_telegram_settings_from_db

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


async def send_reminders():
    from app.bot.bot import get_bot
    bot=get_bot()
    settings=await get_telegram_settings_from_db()
    if not bot or not settings.get('admin_id') or not settings.get('notify_quota'): return
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        clients=(await db.execute(select(Client))).scalars().all()
        for client in clients:
            for suffix,message in reminder_candidates(client,now):
                # Fixed-size keys; replacing a threshold key avoids unbounded accumulation.
                kind=suffix.split(':')[0]+':'+suffix.rsplit(':',1)[-1]
                key=f'notify.client.{client.id}.{kind}'
                row=await db.get(Setting,key)
                if row and row.value==suffix: continue
                try:
                    await bot.send_message(settings['admin_id'],f'Подписка «{client.name}» · #{client.id}: {message}.',parse_mode=None)
                except Exception:
                    logger.warning('Не удалось доставить напоминание: client_id=%s',client.id)
                    continue
                if row: row.value=suffix
                else: db.add(Setting(key=key,value=suffix))
                await db.commit()


async def notification_loop():
    while True:
        try: await send_reminders()
        except asyncio.CancelledError: raise
        except Exception: logger.exception('Ошибка проверки напоминаний')
        await asyncio.sleep(60)
