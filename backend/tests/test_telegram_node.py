from unittest.mock import AsyncMock, patch
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting
from app.bot.bot import BotManager
from app.services.telegram_settings import resolve_telegram_proxy


async def request(auth_headers, payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put('/api/v1/settings/telegram', headers=auth_headers, json=payload)


async def seed(*, active=True):
    async with AsyncSessionLocal() as db:
        db.add(Node(id=1, name='Нода', host='example.com', port=443, protocol='trojan', secret='test', is_enabled=True))
        db.add(Setting(key='telegram_proxy_url', value='socks5://127.0.0.1:10808'))
        if active:
            db.add(Setting(key='active_node_id', value='1'))
        await db.commit()


@pytest.mark.asyncio
async def test_node_mode_validates_and_reloads_same_proxy(auth_headers, monkeypatch):
    monkeypatch.setenv('NODE_PROBE_ENABLED', 'true')
    monkeypatch.setenv('NODE_PROBE_BASE_PORT', '10900')
    await seed()
    with patch('app.api.settings.bot_manager.validate_token', new_callable=AsyncMock) as validate, patch('app.api.settings.bot_manager.reload', new_callable=AsyncMock) as reload:
        response = await request(auth_headers, {'token':'123456:ABCDEFG', 'use_node':True, 'node_id':1})
    assert response.status_code == 200
    assert response.json()['use_node'] is True
    assert response.json()['node_id'] == 1
    validate.assert_awaited_once_with('123456:ABCDEFG', 'socks5://127.0.0.1:10901')
    reload.assert_awaited_once_with('123456:ABCDEFG', 'socks5://127.0.0.1:10901')
    assert response.json()['proxy_url'] == ''


@pytest.mark.asyncio
async def test_failed_validation_does_not_save_node_or_leak_secret(auth_headers, monkeypatch):
    monkeypatch.setenv('NODE_PROBE_ENABLED', 'true')
    await seed()
    with patch('app.api.settings.bot_manager.validate_token', new_callable=AsyncMock, side_effect=RuntimeError('123456:SECRET')), patch('app.api.settings.bot_manager.reload', new_callable=AsyncMock) as reload:
        response = await request(auth_headers, {'token':'123456:SECRET', 'use_node':True, 'node_id':1, 'admin_id':'42'})
    assert response.status_code == 400
    assert 'SECRET' not in response.text
    reload.assert_not_awaited()
    async with AsyncSessionLocal() as db:
        assert await db.get(Setting, 'telegram_use_node') is None
        assert await db.get(Setting, 'telegram_admin_id') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,node_id', [(False, 1), (True, 999), (True, None)])
async def test_unavailable_node_mode_rejected(auth_headers, monkeypatch, enabled, node_id):
    monkeypatch.setenv('NODE_PROBE_ENABLED', 'true' if enabled else 'false')
    await seed(active=node_id == 1)
    response = await request(auth_headers, {'use_node':True, 'node_id':node_id})
    assert response.status_code == 400
    async with AsyncSessionLocal() as db:
        assert await db.get(Setting, 'telegram_use_node') is None


@pytest.mark.asyncio
async def test_partial_update_ignores_manual_proxy_and_preserves_token(auth_headers, monkeypatch):
    monkeypatch.setenv('NODE_PROBE_ENABLED', 'true')
    monkeypatch.setenv('NODE_PROBE_BASE_PORT', '10900')
    await seed()
    with patch('app.api.settings.bot_manager.reload', new_callable=AsyncMock) as reload:
        response = await request(auth_headers, {'notify_failover':False, 'token':''})
    assert response.status_code == 200
    reload.assert_awaited_once_with('', 'socks5://127.0.0.1:10901')
    async with AsyncSessionLocal() as db:
        assert await db.get(Setting, 'telegram_bot_token') is None


@pytest.mark.asyncio
async def test_disabled_node_has_no_direct_fallback(monkeypatch):
    monkeypatch.setenv('NODE_PROBE_ENABLED', 'true')
    await seed()
    async with AsyncSessionLocal() as db:
        node = await db.get(Node, 1)
        node.is_enabled = False
        await db.commit()
        with pytest.raises(ValueError):
            await resolve_telegram_proxy(db, {'use_node':True,'node_id':1,'proxy_url':''})


@pytest.mark.asyncio
async def test_aiogram_socks_session_uses_remote_dns():
    bot = BotManager().create_bot('123456:ABCDEFG', 'socks5://127.0.0.1:10901')
    assert bot.session.proxy == 'socks5://127.0.0.1:10901'
    assert bot.session._connector_init['rdns'] is True
    await bot.session.close()


@pytest.mark.asyncio
async def test_failover_command_applies_config_before_success():
    from app.bot.handlers import cmd_failover
    await seed(active=False)
    message = AsyncMock()
    with patch('app.bot.handlers.apply_active_node', new_callable=AsyncMock, return_value=(False,'ошибка')) as apply:
        await cmd_failover(message)
    assert apply.await_count == 1
    assert 'Текущий выход сохранён' in message.answer.call_args.args[0]
    async with AsyncSessionLocal() as db:
        assert await db.get(Setting, 'active_node_id') is None


@pytest.mark.asyncio
async def test_reload_stops_previous_polling_and_closes_session():
    from app.bot.bot import dp
    manager = BotManager()
    manager.router_setup = True
    fake_bot = AsyncMock()
    cancelled = []

    async def polling(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    with patch.object(manager, 'create_bot', return_value=fake_bot), patch.object(dp, '_polling', side_effect=polling):
        await manager.start('123456:ABCDEFG', 'socks5://127.0.0.1:10901')
        await asyncio.sleep(0)
        await manager.reload('123456:ABCDEFG', '')
        await asyncio.sleep(0)
        await manager.stop()
    assert len(cancelled) == 2
    assert fake_bot.session.close.await_count == 2
    assert manager.polling_task is None
