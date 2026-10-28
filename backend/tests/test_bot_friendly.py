"""Пользовательские сценарии единого меню, без реального Telegram."""
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage,EditMessageText
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Message,Chat,User

from app.bot import chat_screen,handlers,subscriptions,admin_tools,portal,inbox
from app.bot.notification_cards import card
from app.services import telegram_delivery
from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.models.operations import TelegramLink


def message(identifier=10,actor=42,bot=None,text='Текст',is_bot=False):
    result=Message(message_id=identifier,date=datetime.now(timezone.utc),chat=Chat(id=actor,type='private'),
                   from_user=User(id=actor,is_bot=is_bot,first_name='Пользователь'),text=text)
    return result.as_(bot) if bot else result


@pytest.fixture(autouse=True)
def reset_inputs():
    for mapping in (subscriptions.drafts,admin_tools.inputs,admin_tools.pending,handlers.pending_actions,portal.binding_inputs):mapping.clear()
    telegram_delivery._retry_at=0
    yield
    for mapping in (subscriptions.drafts,admin_tools.inputs,admin_tools.pending,handlers.pending_actions,portal.binding_inputs):mapping.clear()


@pytest.mark.asyncio
async def test_processed_input_is_removed_after_successful_handler_only():
    bot=Bot(token='123456:'+('x'*35))
    try:
        event=message(bot=bot,text='Владелец')
        with patch.object(bot,'delete_message',AsyncMock()) as delete, \
             patch.object(handlers,'get_telegram_settings_from_db',AsyncMock(return_value={'admin_id':42})):
            callback=AsyncMock(return_value='готово')
            assert await handlers.check_admin_middleware(callback,event,{})=='готово'
            delete.assert_awaited_once_with(42,10)
            delete.reset_mock()
            callback.side_effect=RuntimeError('network')
            with pytest.raises(RuntimeError):await handlers.check_admin_middleware(callback,event,{})
            delete.assert_not_awaited()
    finally:await bot.session.close()


@pytest.mark.asyncio
async def test_unhandled_input_is_not_deleted_by_either_router():
    bot=Bot(token='123456:'+('x'*35))
    try:
        with patch.object(bot,'delete_message',AsyncMock()) as delete, \
             patch.object(handlers,'get_telegram_settings_from_db',AsyncMock(return_value={'admin_id':42})):
            callback=AsyncMock(return_value=UNHANDLED)
            event=message(bot=bot)
            assert await portal.private_client(callback,event,{}) is UNHANDLED
            assert await handlers.check_admin_middleware(callback,event,{}) is UNHANDLED
            delete.assert_not_awaited()
    finally:await bot.session.close()


@pytest.mark.asyncio
async def test_telegram_refusal_to_delete_does_not_break_new_menu():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(return_value=message(201)),edit_message_text=AsyncMock(),
        delete_message=AsyncMock(side_effect=TelegramBadRequest(method=DeleteMessage(chat_id=42,message_id=100),message="message can't be deleted")))
    await chat_screen.remember(bot,42,'menu',100)
    await chat_screen.live_message(bot,42,'Новое меню',None,channel='menu',new_message=True)
    assert await chat_screen.message_id(bot,42,'menu')==201
    bot.delete_message.assert_awaited_once_with(42,100)


@pytest.mark.asyncio
async def test_notification_updates_existing_menu_and_is_saved_in_inbox():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(return_value=message(200)),edit_message_text=AsyncMock(return_value=message(200)))
    await chat_screen.live_message(bot,42,'Главное меню',None,channel='menu')
    assert await telegram_delivery.send_notice(bot,42,card('🔴','Сбой',['Нет связи']),channel='system')
    assert await telegram_delivery.send_notice(bot,42,card('🟢','Восстановлено',['Связь есть']),channel='system')
    bot.send_message.assert_awaited_once()
    assert bot.edit_message_text.await_count==2
    saved=await inbox.read(bot,42)
    assert len(saved)==1 and 'Восстановлено' in saved[0]['text']
    assert await chat_screen.message_id(bot,42,'system') is None


@pytest.mark.asyncio
async def test_notification_waits_for_wizard_and_confirmation():
    bot=SimpleNamespace(id=123,send_message=AsyncMock(),edit_message_text=AsyncMock())
    subscriptions.drafts[(42,42)]=subscriptions.Draft(42,42,1)
    assert not await telegram_delivery.send_notice(bot,42,card('🔴','Сбой',['Нет связи']),channel='system')
    subscriptions.drafts.clear()
    admin_tools.pending['ticket']={'chat':42,'expires':float('inf')}
    assert not await telegram_delivery.send_notice(bot,42,card('🔴','Сбой',['Нет связи']),channel='system')
    bot.send_message.assert_not_awaited();bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbox_serializes_concurrent_topics():
    bot=SimpleNamespace(id=123)
    import asyncio
    await asyncio.gather(*(inbox.save(bot,42,card('ℹ️',f'Тема {i}',['Текст']),f'topic.{i}') for i in range(5)))
    assert len(await inbox.read(bot,42))==5


@pytest.mark.asyncio
async def test_create_does_not_automatically_send_files_or_links():
    from app.api import clients
    draft=subscriptions.Draft(42,42,1,step='review',values={'name':'Владелец','subscription_period':'month','monthly_traffic_limit':0})
    screen=SimpleNamespace(answer=AsyncMock())
    with patch.object(clients,'create_client_record',AsyncMock(return_value={'client':{'id':7}})), \
         patch.object(admin_tools,'detail',AsyncMock()) as detail,patch.object(subscriptions,'issue_access',AsyncMock()) as issue:
        await subscriptions.create_subscription(screen,draft)
    detail.assert_awaited_once_with(screen,7);issue.assert_not_awaited();screen.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_sees_only_own_subscription_and_both_limits():
    async with AsyncSessionLocal() as db:
        db.add_all([Client(id=1,name='<Моя>',phone='',email='',monthly_traffic_limit=100*1024**3,cdn_monthly_traffic_limit=10*1024**3),
                    Client(id=2,name='Чужая',phone='',email=''),TelegramLink(client_id=1,telegram_id='42')]);await db.commit()
    with patch.object(handlers,'show_screen',AsyncMock()) as show:
        await portal.show_account(message(),42)
    text=show.call_args.args[1]
    assert '&lt;Моя&gt;' in text and 'Чужая' not in text
    assert '100.00 ГБ' in text and '10 ГБ' in text and 'Обход БС' in text
    callback=SimpleNamespace(data='mine:access',from_user=SimpleNamespace(id=42),message=message(),answer=AsyncMock())
    with patch.object(subscriptions,'issue_access',AsyncMock()) as issue:
        await portal.mine(callback)
    issue.assert_awaited_once_with(callback.message,1,awg=False,return_to='mine:home')


@pytest.mark.asyncio
async def test_invalid_quota_keeps_input_for_retry_and_does_not_apply():
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),from_user=SimpleNamespace(id=42),text='не число',answer=AsyncMock())
    admin_tools.inputs[(42,42)]={'kind':'cdnquota','id':1,'expires':float('inf')}
    with patch.object(admin_tools,'prepare',AsyncMock()) as prepare:
        await admin_tools.text_input(screen)
    prepare.assert_not_awaited()
    assert (42,42) in admin_tools.inputs and 'Отправьте' in screen.answer.call_args.args[0]
    screen.text='25,5'
    with patch.object(admin_tools,'prepare',AsyncMock()) as prepare:
        await admin_tools.text_input(screen)
    assert prepare.call_args.args[3]=={'cdn_monthly_traffic_limit':int(25.5*1024**3)}
    assert (42,42) not in admin_tools.inputs


@pytest.mark.asyncio
async def test_bind_button_accepts_plain_code_without_command():
    portal.binding_inputs[(42,42)]=float('inf')
    with patch.object(portal,'bind',AsyncMock()) as bind:
        await portal.text_entry(message(text='one-time-demo'))
    assert bind.call_args.args[0].text=='/bind one-time-demo'
    assert (42,42) not in portal.binding_inputs


@pytest.mark.asyncio
async def test_blocked_client_cannot_request_access_or_awg():
    async with AsyncSessionLocal() as db:
        db.add_all([Client(id=1,name='Приостановлена',phone='',email='',is_active=False),TelegramLink(client_id=1,telegram_id='42')])
        await db.commit()
    with patch.object(handlers,'show_screen',AsyncMock()) as show,patch.object(subscriptions,'issue_access',AsyncMock()) as issue:
        await portal.show_account(message(),42)
        buttons=[button.callback_data for row in show.call_args.args[2].inline_keyboard for button in row]
        assert 'mine:access' not in buttons and 'mine:awg' not in buttons
        for action in ('mine:access','mine:awg'):
            await portal.mine(SimpleNamespace(data=action,from_user=SimpleNamespace(id=42),message=message(),answer=AsyncMock()))
        issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_change_requires_confirmation_and_supports_removal():
    screen=SimpleNamespace(chat=SimpleNamespace(id=42),from_user=SimpleNamespace(id=42),text='-',answer=AsyncMock())
    admin_tools.inputs[(42,42)]={'kind':'email','id':1,'expires':float('inf')}
    with patch.object(admin_tools,'prepare',AsyncMock()) as prepare:
        await admin_tools.text_input(screen)
    assert prepare.call_args.args[3]=={'email':''}
    admin_tools.inputs[(42,42)]={'kind':'name','id':1,'expires':float('inf')}
    screen.text=''
    with patch.object(admin_tools,'prepare',AsyncMock()) as prepare:
        await admin_tools.text_input(screen)
    prepare.assert_not_awaited()
    assert (42,42) in admin_tools.inputs


@pytest.mark.asyncio
async def test_missing_callback_menu_is_recreated_without_extra_screen():
    bot=Bot(token='123456:'+('x'*35))
    try:
        await chat_screen.remember(bot,42,'menu',100)
        missing=TelegramBadRequest(method=EditMessageText(text='Меню',chat_id=42,message_id=100),message='message to edit not found')
        with patch.object(Message,'edit_text',AsyncMock(side_effect=missing)), \
             patch.object(bot,'send_message',AsyncMock(return_value=message(201,bot=bot,is_bot=True))) as send, \
             patch.object(bot,'delete_message',AsyncMock()):
            result=await handlers.show_screen(message(100,bot=bot,is_bot=True),'Меню',handlers.main_menu(),edit=True)
        assert result.message_id==201
        assert await chat_screen.message_id(bot,42,'menu')==201
        send.assert_awaited_once()
    finally:await bot.session.close()
