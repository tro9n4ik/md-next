from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, User, Document

from app.bot import handlers
from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting


def callback(data='md:home', actor=42, message_id=1):
    message = SimpleNamespace(chat=SimpleNamespace(id=42), message_id=message_id,
                              edit_text=AsyncMock(), answer=AsyncMock())
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=actor), message=message, answer=AsyncMock())


@pytest.fixture(autouse=True)
def reset_pending():
    handlers.pending_actions.clear()
    handlers.subscriptions.drafts.clear()
    yield
    handlers.pending_actions.clear()
    handlers.subscriptions.drafts.clear()


def test_menu_has_four_sections_and_short_callback_data():
    menu=handlers.main_menu()
    assert {'sub:new','md:status','md:clients','md:nodes','md:help','ops:list:0','ops:templates','ops:diagnostics'} <= {b.callback_data for row in menu.inline_keyboard for b in row}
    assert all(len(b.callback_data.encode()) <= 64 for row in menu.inline_keyboard for b in row)


@pytest.mark.asyncio
async def test_prepare_creation_does_not_create_client():
    cb=callback('md:new:vless')
    with patch.object(handlers.ClientService, 'create_vless_client', new_callable=AsyncMock) as create:
        await handlers.menu_callback(cb)
    create.assert_not_awaited()
    markup=cb.message.edit_text.call_args.kwargs['reply_markup']
    assert markup.inline_keyboard[0][0].callback_data.startswith('sub:w:')
    assert 'Новая подписка' in cb.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_double_confirmation_creates_once_with_human_actor():
    cb=callback()
    markup=handlers.prepare_action(cb, 'vless')
    cb.data=markup.inline_keyboard[0][0].callback_data
    with patch.object(handlers, 'create_client', new_callable=AsyncMock) as create:
        await handlers.menu_callback(cb)
        await handlers.menu_callback(cb)
    create.assert_awaited_once_with(cb.message, 42, 'vless')
    assert cb.answer.call_args.kwargs['show_alert'] is True


def test_ticket_bound_to_actor_and_message_and_expires(monkeypatch):
    cb=callback()
    cb.data=handlers.prepare_action(cb,'awg').inline_keyboard[0][0].callback_data
    foreign=callback(cb.data,actor=99)
    assert handlers.consume_action(foreign) is None
    copied=callback(cb.data,message_id=2)
    assert handlers.consume_action(copied) is None
    pending=next(iter(handlers.pending_actions.values()))
    monkeypatch.setattr(handlers.time,'monotonic',lambda:pending.expires_at+1)
    assert handlers.consume_action(cb) is None


@pytest.mark.asyncio
async def test_cancel_invalidates_confirmation():
    cb=callback()
    token_data=handlers.prepare_action(cb,'node',1).inline_keyboard[0][0].callback_data
    await handlers.menu_callback(cb)
    cb.data=token_data
    assert handlers.consume_action(cb) is None


@pytest.mark.asyncio
async def test_callback_admin_check_and_private_dialog():
    async with AsyncSessionLocal() as db:
        db.add(Setting(key='telegram_admin_id',value='42'));await db.commit()
    handler=AsyncMock()
    def event(actor, chat_type='private'):
        return CallbackQuery(id='test',chat_instance='test',data='md:home',from_user=User(id=actor,is_bot=False,first_name='Администратор'),
            message=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=42,type=chat_type)))
    with patch.object(CallbackQuery,'answer',new_callable=AsyncMock) as answer:
        await handlers.check_admin_middleware(handler,event(99),{})
        handler.assert_not_awaited()
        assert answer.call_args.kwargs['show_alert'] is True
        await handlers.check_admin_middleware(handler,event(42,'group'),{})
        handler.assert_not_awaited()
        await handlers.check_admin_middleware(handler,event(42),{})
    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_node_disappears_before_confirmation_preserves_route():
    cb=callback()
    cb.data=handlers.prepare_action(cb,'node',999).inline_keyboard[0][0].callback_data
    with patch.object(handlers,'apply_active_node',new_callable=AsyncMock) as apply:
        await handlers.menu_callback(cb)
    apply.assert_not_awaited()
    assert 'Текущий выход сохранён' in cb.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_node_confirmation_applies_selected_node():
    async with AsyncSessionLocal() as db:
        db.add(Node(id=7,name='<DE & узел>',host='example.com',port=443,protocol='trojan',secret='test',is_active=True,is_enabled=True));await db.commit()
    cb=callback()
    cb.data=handlers.prepare_action(cb,'node',7).inline_keyboard[0][0].callback_data
    with patch.object(handlers,'apply_active_node',new_callable=AsyncMock,return_value=(True,'ok')) as apply:
        await handlers.menu_callback(cb)
    assert apply.call_args.args[1].id == 7
    assert '&lt;DE &amp; узел&gt;' in cb.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_status_escapes_node_name_and_noop_refresh():
    async with AsyncSessionLocal() as db:
        db.add(Node(id=7,name='<DE & узел>',host='example.com',port=443,protocol='trojan',secret='test',is_enabled=True))
        db.add(Setting(key='active_node_id',value='7'));await db.commit()
    assert '&lt;DE &amp; узел&gt;' in await handlers.status_text()
    message=SimpleNamespace(edit_text=AsyncMock(side_effect=TelegramBadRequest(method=EditMessageText(chat_id=42,message_id=1,text='test'),message='message is not modified')))
    await handlers.show_screen(message,'test',handlers.back_menu(),edit=True)


@pytest.mark.asyncio
async def test_back_from_configuration_file_opens_text_menu():
    message=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=42,type='private'),
                    document=Document(file_id='test',file_unique_id='test'),caption='Конфигурация')
    with patch.object(Message,'answer',new_callable=AsyncMock) as answer, patch.object(Message,'edit_text',new_callable=AsyncMock) as edit:
        await handlers.show_screen(message,'Меню',handlers.main_menu(),edit=True)
    answer.assert_awaited_once()
    edit.assert_not_awaited()
