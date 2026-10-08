from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import asyncio

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Message, Chat, User

from app.bot import chat_screen, handlers, admin_tools
from app.bot import subscriptions


def message(identifier, *, bot=None, actor=42, is_bot=False):
    value=Message(message_id=identifier,date=datetime.now(timezone.utc),chat=Chat(id=42,type='private'),
                  from_user=User(id=actor,is_bot=is_bot,first_name='Demo'),text='Меню')
    return value.as_(bot) if bot else value


@pytest.mark.asyncio
async def test_live_card_is_edited_after_reload_not_sent_again():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(return_value=message(100)),edit_message_text=AsyncMock(return_value=message(100)))
    await chat_screen.live_message(bot,42,'Сбой',None,channel='system')
    other=SimpleNamespace(id=123,send_message=AsyncMock(),edit_message_text=AsyncMock(return_value=message(100)))
    await chat_screen.live_message(other,42,'Восстановлена',None,channel='system')
    bot.send_message.assert_awaited_once()
    other.send_message.assert_not_awaited()
    assert other.edit_message_text.call_args.kwargs['message_id']==100


@pytest.mark.asyncio
async def test_deleted_card_is_replaced_but_network_failure_does_not_duplicate():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(return_value=message(100)),edit_message_text=AsyncMock())
    await chat_screen.live_message(bot,42,'Сбой',None,channel='system')
    bot.edit_message_text.side_effect=RuntimeError('network')
    with pytest.raises(RuntimeError):await chat_screen.live_message(bot,42,'Восстановлена',None,channel='system')
    assert bot.send_message.await_count==1
    bot.edit_message_text.side_effect=TelegramBadRequest(method=EditMessageText(chat_id=42,message_id=100,text='x'),message='message to edit not found')
    bot.send_message.return_value=message(101)
    await chat_screen.live_message(bot,42,'Восстановлена',None,channel='system')
    assert await chat_screen.message_id(bot,42,'system')==101
    assert bot.send_message.await_count==2


@pytest.mark.asyncio
async def test_noop_edit_and_distinct_bot_identity():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(return_value=message(100)),edit_message_text=AsyncMock())
    await chat_screen.live_message(bot,42,'Сбой',None,channel='system')
    bot.edit_message_text.side_effect=TelegramBadRequest(method=EditMessageText(chat_id=42,message_id=100,text='x'),message='message is not modified')
    await chat_screen.live_message(bot,42,'Сбой',None,channel='system')
    assert bot.send_message.await_count==1
    replacement=SimpleNamespace(id=124,send_message=AsyncMock(return_value=message(101)),edit_message_text=AsyncMock())
    await chat_screen.live_message(replacement,42,'Сбой',None,channel='system')
    replacement.send_message.assert_awaited_once()
    replacement.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_updates_create_only_one_card():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(return_value=message(100)),edit_message_text=AsyncMock(return_value=message(100)))
    await asyncio.gather(*(chat_screen.live_message(bot,42,str(i),None,channel='system') for i in range(4)))
    assert bot.send_message.await_count==1
    assert bot.edit_message_text.await_count==3


@pytest.mark.asyncio
async def test_subscription_wizard_keeps_actual_reused_message_id():
    bot=Bot(token='123456:'+('x'*35))
    subscriptions.drafts.clear()
    try:
        with patch.object(bot,'send_message',AsyncMock(return_value=message(200,bot=bot,is_bot=True,actor=bot.id))) as send, patch.object(bot,'edit_message_text',AsyncMock(return_value=message(200,bot=bot,is_bot=True,actor=bot.id))) as edit:
            await subscriptions.start(message(10,bot=bot),42)
            draft=subscriptions.get_draft(42,42)
            assert draft.actor==42 and draft.message==200
            await subscriptions.text_input(message(11,bot=bot).model_copy(update={'text':'Владелец'}))
            assert draft.message==200 and draft.step=='phone'
            assert send.await_count==1 and edit.await_count==1
    finally:
        subscriptions.drafts.clear()
        await bot.session.close()


@pytest.mark.asyncio
async def test_menu_reuses_pointer_and_does_not_overwrite_notification():
    bot=Bot(token='123456:'+('x'*35))
    try:
        with patch.object(bot,'send_message',AsyncMock(return_value=message(200,bot=bot,is_bot=True,actor=bot.id))) as send, patch.object(bot,'edit_message_text',AsyncMock(return_value=message(200,bot=bot,is_bot=True,actor=bot.id))) as edit:
            await handlers.show_screen(message(10,bot=bot),'Меню',None)
            await handlers.show_screen(message(11,bot=bot),'Статус',None)
            assert send.await_count==1 and edit.await_count==1
            await chat_screen.remember(bot,42,'system',99)
            await handlers.show_screen(message(99,bot=bot,is_bot=True,actor=bot.id),'Подписки',None,edit=True)
            assert edit.call_args.kwargs['message_id']==200
            assert await chat_screen.message_id(bot,42,'system')==99
    finally:await bot.session.close()


def test_replacing_screen_invalidates_old_confirmation_but_keeps_current():
    admin_tools.pending.clear()
    admin_tools.pending['old']={'chat':42,'message':200}
    admin_tools.pending['current']={'chat':42,'message':200}
    handlers.invalidate_screen_actions(42,200,handlers.keyboard([('Подтвердить','ops:confirm:current')]))
    assert set(admin_tools.pending)=={'current'}
    handlers.invalidate_screen_actions(42,200,handlers.main_menu())
    assert not admin_tools.pending


@pytest.mark.asyncio
async def test_diagnostics_edits_screen_and_file_requires_explicit_owner_click():
    admin_tools.diagnostic_reports.clear()
    report={'checks':[{'name':'DNS <demo>','ok':True,'detail':'Works & ready'}],'notice':'Серверная проверка'}
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),message_id=200,from_user=SimpleNamespace(id=42),answer_document=AsyncMock(),edit_text=AsyncMock())
    with patch('app.services.diagnostics.run_diagnostics',AsyncMock(return_value=report)), patch.object(handlers,'show_screen',AsyncMock(return_value=screen)) as render:
        await admin_tools.diagnosis(screen)
    assert render.await_count==2
    screen.answer_document.assert_not_awaited()
    final=render.call_args.args[1]
    assert '&lt;demo&gt;' in final and '&amp;' in final
    token=next(iter(admin_tools.diagnostic_reports))
    cb=SimpleNamespace(data='ops:diag_file:'+token,from_user=SimpleNamespace(id=42),message=screen,answer=AsyncMock())
    await admin_tools.callback(cb)
    screen.answer_document.assert_awaited_once()
    screen.answer_document.reset_mock()
    screen.answer=AsyncMock()
    cb.from_user.id=99
    await admin_tools.callback(cb)
    screen.answer_document.assert_not_awaited()
    admin_tools.diagnostic_reports.clear()
