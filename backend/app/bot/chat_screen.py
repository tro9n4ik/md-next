"""Reuse bot-owned text messages; never delete the user's chat history."""
import asyncio
from collections import defaultdict

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting

locks = defaultdict(asyncio.Lock)


def screen_key(bot, chat_id, channel):
    bot_id = getattr(bot, 'id', 0)
    return f'telegram.live.{bot_id if isinstance(bot_id, int) else 0}.{chat_id}.{channel}'


async def message_id(bot, chat_id, channel):
    async with AsyncSessionLocal() as db:
        row = await db.get(Setting, screen_key(bot, chat_id, channel))
        return int(row.value) if row and row.value.isdigit() else None


async def remember(bot, chat_id, channel, value):
    if not isinstance(value, int):
        return
    async with AsyncSessionLocal() as db:
        key = screen_key(bot, chat_id, channel)
        row = await db.get(Setting, key)
        if row:
            row.value = str(value)
        else:
            db.add(Setting(key=key, value=str(value)))
        await db.commit()


async def protected_message(bot, chat_id, identifier):
    prefix = screen_key(bot, chat_id, '')
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Setting).where(Setting.key.startswith(prefix), Setting.key != prefix+'menu'))).scalars().all()
        return any(row.value == str(identifier) for row in rows)


async def live_message(bot, chat_id, text, markup, *, channel, silent=False):
    key = screen_key(bot, chat_id, channel)
    async with locks[key]:
        previous = await message_id(bot, chat_id, channel)
        if previous:
            try:
                result = await bot.edit_message_text(text, chat_id=chat_id, message_id=previous,
                                                     parse_mode='HTML', reply_markup=markup)
                return result, previous
            except TelegramBadRequest as exc:
                reason = exc.message.lower()
                if 'message is not modified' in reason:
                    return None, previous
                if not any(s in reason for s in ('message to edit not found', "message can't be edited", 'message_id_invalid')):
                    raise
        result = await bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup,
                                        disable_notification=silent)
        identifier = getattr(result, 'message_id', None)
        await remember(bot, chat_id, channel, identifier)
        return result, identifier
