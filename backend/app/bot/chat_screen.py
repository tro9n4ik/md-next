"""Один экран бота и очистка обработанного ввода в личном диалоге."""
import asyncio
from collections import defaultdict

from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from sqlalchemy import select
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting

locks = defaultdict(asyncio.Lock)


async def discard(bot, chat_id, identifier):
    """Удаляем только известный ID. Отказ очистки не ломает меню."""
    if not isinstance(identifier, int) or identifier <= 0:
        return
    if not callable(getattr(bot,'delete_message',None)):
        return
    try:
        await asyncio.wait_for(bot.delete_message(chat_id, identifier),5)
    except (TelegramAPIError,asyncio.TimeoutError):
        pass


async def clean_input(message):
    if message.chat.type != 'private' or not message.from_user or message.from_user.is_bot:
        return
    try:
        bot = message.bot
    except (RuntimeError, AttributeError):
        return
    await discard(bot, message.chat.id, message.message_id)


async def clear_attachment(bot, chat_id):
    identifier = await message_id(bot, chat_id, 'attachment')
    if identifier:
        await discard(bot, chat_id, identifier)
        await remember(bot,chat_id,'attachment',0)


async def cleanup_legacy_cards(bot,chat_id):
    prefix=screen_key(bot,chat_id,'')
    current=await message_id(bot,chat_id,'menu')
    async with AsyncSessionLocal() as db:
        rows=(await db.execute(select(Setting).where(Setting.key.startswith(prefix),
              Setting.key.notin_([prefix+'menu',prefix+'attachment'])).limit(40))).scalars().all()
        identifiers=[int(row.value) for row in rows if row.value.isdigit() and int(row.value)!=current]
        for row in rows:
            await db.delete(row)
        await db.commit()
    await asyncio.gather(*(discard(bot,chat_id,identifier) for identifier in identifiers))


async def remember_attachment(message, sent):
    try:
        bot = message.bot
    except (RuntimeError, AttributeError):
        return
    await clear_attachment(bot, message.chat.id)
    await remember(bot, message.chat.id, 'attachment', getattr(sent, 'message_id', None))


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


async def live_message(bot, chat_id, text, markup, *, channel, silent=False, new_message=False):
    key = screen_key(bot, chat_id, channel)
    async with locks[key]:
        # После очистки истории Telegram может разрешать редактирование старой
        # карточки, которая уже не видна пользователю. Команде нужен новый ответ.
        previous = await message_id(bot, chat_id, channel)
        if previous and not new_message:
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
        if channel == 'menu' and previous and identifier != previous:
            await discard(bot, chat_id, previous)
        return result, identifier
