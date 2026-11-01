"""Настройка из панели перекрывает env и сохраняет прежний маршрут при ошибке."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
import pytest

from app.api.auth import create_access_token
from app.main import app
from app.models.node import Node
from app.models.setting import Setting
from app.models.user import User
from app.services import node_uplink
from app.services.node_uplink import local_listener as real_local_listener
from app.services.client_service import ClientService
from app.services.watchdog import WatchdogService
from app.services.warp import WarpService
from app.services.xray import XrayService
from conftest import TestingSessionLocal


@pytest.fixture(autouse=True)
def isolated_local_state(monkeypatch):
    monkeypatch.delenv('NODE_UPLINK_WARP_PORT', raising=False)
    monkeypatch.setattr(node_uplink, 'local_listener', AsyncMock(return_value=True))
    monkeypatch.setattr(node_uplink, 'runtime_request', AsyncMock(return_value={'phase': 'ready', 'installed': True}))


async def request(method, path, headers, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        return await api.request(method, '/api/v1/warp/uplink'+path, headers=headers, **kwargs)


async def seed(*settings):
    async with TestingSessionLocal() as db:
        node = Node(name='Тестовая нода', host='192.0.2.1', port=24443, protocol='trojan', secret='synthetic',
                    is_active=True, is_enabled=True, status='healthy')
        db.add(node)
        await db.flush()
        db.add(Setting(key='active_node_id', value=str(node.id)))
        db.add_all([Setting(key=key, value=value) for key, value in settings])
        await db.commit()
        return node.id


@pytest.mark.asyncio
async def test_default_is_off_and_existing_environment_is_visible(auth_headers, monkeypatch):
    await seed()
    response = await request('GET', '', auth_headers)
    assert response.json()['enabled'] is False
    monkeypatch.setenv('NODE_UPLINK_WARP_PORT', '41000')
    response = await request('GET', '', auth_headers)
    assert response.json()['enabled'] is True
    assert response.json()['port'] == 41000
    assert response.json()['source'] == 'environment'


@pytest.mark.asyncio
async def test_disable_overrides_env_in_config_watchdog_and_node_checks(auth_headers, monkeypatch):
    monkeypatch.setenv('NODE_UPLINK_WARP_PORT', '40000')
    node_id = await seed()
    async def applied(**kwargs):
        config = json.loads(XrayService.generate_config(**kwargs))
        assert not any(o['tag'] == 'uplink-warp' for o in config['outbounds'])
        assert all('dialerProxy' not in o.get('streamSettings', {}).get('sockopt', {}) for o in config['outbounds'])
        return True, 'ok'
    with patch('app.services.client_service.apply_reality_sni', new=AsyncMock()), \
         patch.object(XrayService, 'apply_config', new=AsyncMock(side_effect=applied)), \
         patch('app.services.mtproto.sync_config', new=AsyncMock(return_value=(True, 'ok'))), \
         patch.object(WarpService, 'disconnect', new=AsyncMock()) as disconnect:
        response = await request('PUT', '', auth_headers, json={'enabled': False})
    assert response.status_code == 200, response.text
    assert response.json()['enabled'] is False
    disconnect.assert_not_awaited()
    assert node_uplink.runtime_request.call_args.args == ('status',)
    async with TestingSessionLocal() as db:
        assert await node_uplink.get_uplink_port(db) is None
        watchdog = WatchdogService()
        with patch.object(watchdog, '_check_node_ping', new=AsyncMock(return_value=(True, 10))):
            await watchdog._update_all_nodes_ping(db)
        assert watchdog._uplink_port is None
        with patch('app.services.warp_node.test_node_proxy', new=AsyncMock(return_value={})) as probe:
            await WarpService.test_target(db)
            assert probe.call_args.kwargs['uplink_port'] is None
            await WarpService.control_node(await db.get(Node, node_id), 'status', True, db=db)
            assert probe.call_args.kwargs['uplink_port'] is None
    assert node_uplink.node_uplink_warp_port() == 40000


@pytest.mark.asyncio
async def test_enable_checks_local_warp_and_the_node_before_persisting(auth_headers):
    node_id = await seed()
    with patch.object(WarpService, 'test_proxy', new=AsyncMock(return_value={'warp': 'on'})) as local, \
         patch('app.services.warp_node.test_node_proxy', new=AsyncMock(return_value={})) as node, \
         patch.object(ClientService, 'sync_xray_clients', new=AsyncMock(return_value=(True, 'ok'))):
        response = await request('PUT', '', auth_headers, json={'enabled': True})
    assert response.status_code == 200, response.text
    assert response.json()['enabled'] is True
    local.assert_awaited_once_with(40000, timeout=5)
    assert node.call_args.kwargs['control'] is True
    assert node.call_args.kwargs['uplink_port'] == 40000
    async with TestingSessionLocal() as db:
        assert await node_uplink.get_uplink_port(db) == 40000
        assert json.loads((await db.get(Setting, node_uplink.CHECK_KEY)).value)['node_id'] == node_id


@pytest.mark.asyncio
async def test_bad_local_proxy_does_not_change_route_or_enable_setting(auth_headers):
    await seed()
    with patch.object(WarpService, 'test_proxy', new=AsyncMock(return_value={'warp': 'off'})), \
         patch('app.services.warp_node.test_node_proxy', new=AsyncMock()) as node, \
         patch.object(ClientService, 'sync_xray_clients', new=AsyncMock()) as sync:
        response = await request('PUT', '', auth_headers, json={'enabled': True})
    assert response.status_code == 409
    node.assert_not_awaited()
    sync.assert_not_awaited()
    async with TestingSessionLocal() as db:
        assert await db.get(Setting, node_uplink.ENABLED_KEY) is None
        assert await node_uplink.get_uplink_port(db) is None


@pytest.mark.asyncio
async def test_apply_failure_restores_stored_settings_and_service_config(auth_headers):
    await seed((node_uplink.ENABLED_KEY, 'true'), (node_uplink.PORT_KEY, '41000'))
    with patch.object(ClientService, 'sync_xray_clients', new=AsyncMock(return_value=(False, 'test failure'))), \
         patch.object(ClientService, 'restore_committed_configs', new=AsyncMock()) as restore:
        response = await request('PUT', '', auth_headers, json={'enabled': False})
    assert response.status_code == 502
    restore.assert_awaited_once()
    async with TestingSessionLocal() as db:
        assert await node_uplink.get_uplink_port(db) == 41000


@pytest.mark.asyncio
async def test_preparation_uses_selected_node_and_does_not_enable_traffic(auth_headers):
    node_id = await seed()
    node_uplink.runtime_request.return_value = {'phase': 'queued', 'installed': False}
    with patch.object(ClientService, 'sync_xray_clients', new=AsyncMock()) as sync:
        response = await request('POST', '/prepare', auth_headers)
    assert response.status_code == 200
    node_uplink.runtime_request.assert_awaited_once_with('prepare', node_id=node_id, port=40000)
    sync.assert_not_awaited()
    async with TestingSessionLocal() as db:
        assert await db.get(Setting, node_uplink.ENABLED_KEY) is None


@pytest.mark.asyncio
async def test_preparation_without_node_is_rejected(auth_headers):
    response = await request('POST', '/prepare', auth_headers)
    assert response.status_code == 409
    node_uplink.runtime_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_requires_a_real_boolean(auth_headers):
    response = await request('PUT', '', auth_headers, json={'enabled': 'false'})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_pending_preparation_prevents_enabling_but_allows_disabling(auth_headers):
    await seed((node_uplink.ENABLED_KEY, 'true'))
    node_uplink.runtime_request.return_value = {'phase': 'registering', 'installed': True}
    with patch.object(ClientService, 'sync_xray_clients', new=AsyncMock(return_value=(True, 'ok'))), \
         patch.object(WarpService, 'test_proxy', new=AsyncMock()) as probe:
        response = await request('PUT', '', auth_headers, json={'enabled': True})
        assert response.status_code == 409
        response = await request('PUT', '', auth_headers, json={'enabled': False})
        assert response.status_code == 200
        assert response.json()['enabled'] is False
        probe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path', [('GET', ''), ('PUT', ''), ('POST', '/prepare'), ('POST', '/check')])
async def test_subadmins_cannot_manage_the_server_uplink(method, path):
    async with TestingSessionLocal() as db:
        db.add(User(username='limited', hashed_password='not-a-login-hash', role='subadmin', token_version=1))
        await db.commit()
    headers = {'Authorization': 'Bearer '+create_access_token({'sub': 'limited'})}
    assert (await request(method, path, headers, json={'enabled': True})).status_code == 403
    node_uplink.runtime_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_result_is_hidden_after_the_selected_node_changes(auth_headers):
    node_id = await seed()
    async with TestingSessionLocal() as db:
        db.add(Setting(key=node_uplink.CHECK_KEY, value=json.dumps({'node_id': node_id+1, 'port': 40000, 'ok': True})))
        await db.commit()
    response = await request('GET', '', auth_headers)
    assert response.json()['check'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [OSError, asyncio.TimeoutError])
async def test_local_proxy_failure_is_a_status_on_all_supported_python_versions(error):
    with patch('app.services.node_uplink.asyncio.open_connection', new=AsyncMock(side_effect=error)):
        assert await real_local_listener(40000) is False
