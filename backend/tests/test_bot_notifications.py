from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import select

from app.bot.notification_cards import card, node_down, node_recovered, route_changed
from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.models.node import Node
from app.models.setting import Setting
from app.services import notifications, telegram_delivery as delivery
from app.services.watchdog import WatchdogService


SETTINGS = {'admin_id': 42, 'notify_node_down': True, 'notify_failover': True, 'notify_quota': True}


@pytest.fixture(autouse=True)
def reset_delivery():
    delivery._retry_at = 0
    yield
    delivery._retry_at = 0


def test_cards_distinguish_failure_recovery_and_route_and_escape_names():
    node = SimpleNamespace(name='<DE & backup>')
    failed = node_down(node, reason='port', ping_ms=0, threshold=300, checks=3)
    assert '0 мс' not in failed.text
    assert '&lt;DE &amp; backup&gt;' in failed.text
    recovered = node_recovered(node, 125, 3)
    assert '2 мин 5 сек' in recovered.text
    assert recovered.silent and not failed.silent
    changed = route_changed(node, None, 'all_nodes_unhealthy')
    assert 'Было:' in changed.text and 'Стало:' in changed.text
    assert 'Все ноды недоступны' in changed.text
    assert not changed.silent


@pytest.mark.asyncio
async def test_queue_survives_missing_bot_and_coalesces_latest_state():
    with patch.object(delivery, 'get_telegram_settings_from_db', AsyncMock(return_value=SETTINGS)), patch('app.bot.bot.get_bot', return_value=None):
        await delivery.enqueue(card('🔴', 'Сбой', ['Первая проверка']), 'node_down', event_key='node.1')
        await delivery.deliver_pending()
        await delivery.enqueue(card('🟢', 'Восстановлена', ['Последнее состояние']), 'node_down', event_key='node.1')
        async with AsyncSessionLocal() as db:
            rows=(await db.execute(select(Setting).where(Setting.key.startswith(delivery.PREFIX)))).scalars().all()
            assert len(rows)==1 and 'Восстановлена' in rows[0].value
        bot=SimpleNamespace(send_message=AsyncMock())
        with patch('app.bot.bot.get_bot', return_value=bot), patch.object(delivery.asyncio, 'sleep', AsyncMock()):
            await delivery.deliver_pending()
            await delivery.deliver_pending()
        assert bot.send_message.await_count==1
        assert 'Последнее состояние' in bot.send_message.call_args.args[1]


@pytest.mark.asyncio
async def test_transport_failure_keeps_pending_and_respects_retry_after():
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=TelegramRetryAfter(method=SendMessage(chat_id=42,text='x'), message='flood', retry_after=60)))
    with patch.object(delivery, 'get_telegram_settings_from_db', AsyncMock(return_value=SETTINGS)), patch('app.bot.bot.get_bot', return_value=bot):
        await delivery.enqueue(card('🔴','Сбой',['Нет связи']))
        await delivery.deliver_pending()
        await delivery.deliver_pending()
        assert bot.send_message.await_count==1
        async with AsyncSessionLocal() as db:
            assert await db.get(Setting, delivery.PREFIX+'route') is not None
        delivery._retry_at=0
        bot.send_message.side_effect=None
        with patch.object(delivery.asyncio,'sleep',AsyncMock()):await delivery.deliver_pending()
        assert bot.send_message.await_count==2


@pytest.mark.asyncio
async def test_pending_is_not_forwarded_to_new_admin_or_disabled_category():
    bot=SimpleNamespace(send_message=AsyncMock())
    with patch.object(delivery,'get_telegram_settings_from_db',AsyncMock(return_value=SETTINGS)):
        await delivery.enqueue(card('🔴','Сбой',['x']))
    with patch.object(delivery,'get_telegram_settings_from_db',AsyncMock(return_value={**SETTINGS,'admin_id':99})), patch('app.bot.bot.get_bot',return_value=bot):
        await delivery.deliver_pending()
    bot.send_message.assert_not_awaited()
    with patch.object(delivery,'get_telegram_settings_from_db',AsyncMock(return_value={**SETTINGS,'notify_failover':False})):
        assert not await delivery.enqueue(card('🔴','Сбой',['x']))


@pytest.mark.asyncio
async def test_new_state_arriving_during_delivery_is_not_deleted():
    async def sending(*args, **kwargs):
        await delivery.enqueue(card('🟢','Новое состояние',['Связь восстановлена']),event_key='route')
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=sending))
    with patch.object(delivery,'get_telegram_settings_from_db',AsyncMock(return_value=SETTINGS)), patch('app.bot.bot.get_bot',return_value=bot), patch.object(delivery.asyncio,'sleep',AsyncMock()):
        await delivery.enqueue(card('🔴','Первое состояние',['Связи нет']))
        await delivery.deliver_pending()
        async with AsyncSessionLocal() as db:
            row=await db.get(Setting,delivery.PREFIX+'route')
            assert row is not None and 'Новое состояние' in row.value


@pytest.mark.asyncio
async def test_enqueue_with_health_transaction_rolls_back_together():
    with patch.object(delivery,'get_telegram_settings_from_db',AsyncMock(return_value=SETTINGS)):
        async with AsyncSessionLocal() as db:
            await delivery.enqueue(card('🔴','Сбой',['x']),session=db,event_key='node.9')
            await db.flush()
            await db.rollback()
        async with AsyncSessionLocal() as db:
            assert await db.get(Setting,delivery.PREFIX+'node.9') is None


@pytest.mark.asyncio
async def test_digest_groups_clients_and_marks_only_successful_delivery():
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        db.add_all([Client(name=f'Клиент <{i}>',phone='',email='',created_at=now,is_active=True,expires_at=now+timedelta(hours=1)) for i in range(3)])
        await db.commit()
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('offline')))
    with patch('app.bot.bot.get_bot',return_value=bot), patch.object(notifications,'get_telegram_settings_from_db',AsyncMock(return_value=SETTINGS)):
        await notifications.send_reminders()
        async with AsyncSessionLocal() as db:
            assert not (await db.execute(select(Setting).where(Setting.key.startswith('notify.client.')))).scalars().all()
        bot.send_message.side_effect=None
        delivery._retry_at=0
        await notifications.send_reminders()
        await notifications.send_reminders()
    assert bot.send_message.await_count==2
    text=bot.send_message.call_args.args[1]
    assert text.count('Осталось меньше суток')==3
    assert '&lt;0&gt;' in text
    assert bot.send_message.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data=='ops:list:0'
    assert bot.send_message.call_args.kwargs['disable_notification'] is True


@pytest.mark.asyncio
async def test_large_digest_chunks_and_higher_threshold_covers_lower():
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        db.add_all([Client(name='Очень длинное имя '*30,phone='',email='',created_at=now,is_active=True,expires_at=now-timedelta(hours=1)) for _ in range(23)])
        await db.commit()
    bot=SimpleNamespace(send_message=AsyncMock())
    with patch('app.bot.bot.get_bot',return_value=bot), patch.object(notifications,'get_telegram_settings_from_db',AsyncMock(return_value=SETTINGS)), patch.object(notifications.asyncio,'sleep',AsyncMock()):
        await notifications.send_reminders()
        await notifications.send_reminders()
    assert bot.send_message.await_count==3
    assert all(len(call.args[1])<4096 for call in bot.send_message.call_args_list)
    assert all(not call.kwargs['disable_notification'] for call in bot.send_message.call_args_list)
    async with AsyncSessionLocal() as db:
        rows=(await db.execute(select(Setting).where(Setting.key.startswith('notify.client.')))).scalars().all()
        assert len(rows)==23*3


@pytest.mark.asyncio
async def test_recovery_notice_waits_for_stable_checks_without_changing_health():
    now=datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        node=Node(name='DE',host='example.com',port=443,protocol='trojan',secret='test',is_enabled=True,is_active=False,status='unhealthy')
        db.add(node);await db.flush()
        db.add(Setting(key=f'notify.node.{node.id}',value=(now-timedelta(minutes=2)).isoformat()))
        await db.commit()
        watchdog=WatchdogService()
        with patch.object(watchdog,'_check_node_ping',AsyncMock(return_value=(True,10))), patch.object(watchdog,'_probe_node_egress',AsyncMock(return_value=None)), patch.object(watchdog,'_update_country',AsyncMock()), patch('app.services.watchdog.notify_admin',AsyncMock()) as notify:
            await watchdog._update_all_nodes_ping(db)
            assert node.status=='healthy'
            notify.assert_not_awaited()
            await watchdog._update_all_nodes_ping(db)
            notify.assert_not_awaited()
            await watchdog._update_all_nodes_ping(db)
            notify.assert_awaited_once()
            assert 'Нода восстановлена' in notify.call_args.args[1].text
