"""Persistent, coalesced notifications; delivery uses the bot's current node."""
import asyncio
import json
import logging
import time

from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import select, delete

from app.bot.notification_cards import Notice, card, safe
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting
from app.services.telegram_settings import get_telegram_settings_from_db

logger = logging.getLogger(__name__)
PREFIX = "notify.pending."
_lock = asyncio.Lock()
_retry_at = 0.0


async def enqueue(notice, category="failover", *, session=None, event_key="route"):
    settings = await get_telegram_settings_from_db()
    if not settings.get("admin_id") or not settings.get("notify_" + category, False):
        return False
    if isinstance(notice, str):
        notice = card("ℹ️", "Состояние системы", [safe(notice, 2000)])
    payload = json.dumps({"text": notice.text, "action": notice.action, "button": notice.button,
                          "silent": notice.silent, "category": category, "admin_id": settings["admin_id"]}, ensure_ascii=False)
    key = PREFIX + event_key

    async def store(db):
        row = await db.get(Setting, key)
        if row:
            row.value = payload
        else:
            db.add(Setting(key=key, value=payload))

    if session is not None:
        await store(session)  # Commit together with the health transition.
    else:
        async with AsyncSessionLocal() as db:
            await store(db)
            await db.commit()
    return True


async def send_notice(bot, admin_id, notice, *, channel=None):
    """A single delivery gate also handles Telegram's flood control for digests."""
    global _retry_at
    if bot is None or time.monotonic() < _retry_at:
        return False
    try:
        if channel:
            from app.bot.chat_screen import live_message
            await live_message(bot, admin_id, notice.text, notice.markup(), channel=channel, silent=notice.silent)
        else:
            await bot.send_message(admin_id, notice.text, parse_mode="HTML", reply_markup=notice.markup(),
                                   disable_notification=notice.silent)
        return True
    except TelegramRetryAfter as exc:
        _retry_at = time.monotonic() + exc.retry_after
        logger.warning("Telegram ограничил частоту уведомлений; отложена повторная доставка")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _retry_at = time.monotonic() + 30
        # Transport exceptions can contain the bot token; never log their text.
        logger.warning("Доставка Telegram отложена: %s", type(exc).__name__)
    return False


async def deliver_pending():
    from app.bot.bot import get_bot
    async with _lock:
        settings = await get_telegram_settings_from_db()
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Setting).where(Setting.key.startswith(PREFIX)).order_by(Setting.key).limit(10))).scalars().all()
            await db.commit()  # Do not hold a DB transaction during a network request.
            for row in rows:
                try:
                    data = json.loads(row.value)
                    enabled = settings.get("notify_" + data["category"], False)
                    # Never transfer pending messages to a newly configured administrator.
                    if not enabled or data["admin_id"] != settings.get("admin_id"):
                        await db.delete(row)
                        await db.commit()
                        continue
                    notice = Notice(data["text"], data["action"], data["button"], data["silent"])
                except (ValueError, KeyError, TypeError):
                    logger.warning("Пропущено повреждённое уведомление: %s", row.key)
                    await db.delete(row)
                    await db.commit()
                    continue
                if not await send_notice(get_bot(), settings["admin_id"], notice, channel='system'):
                    break
                # An updated card can arrive while Telegram is sending the previous one.
                await db.execute(delete(Setting).where(Setting.key == row.key, Setting.value == row.value))
                await db.commit()
                await asyncio.sleep(1.1)  # One admin chat; avoid a burst after an outage.
