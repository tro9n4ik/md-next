import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.node import Node
from app.models.setting import Setting
from app.services.warp import WarpService
from conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_ipv4_setting_selects_native_port_after_failover():
    async with TestingSessionLocal() as db:
        db.add(Setting(key='warp.ipv4_only', value='false'))
        await db.commit()
        for node_id in (1, 2):
            with patch('app.services.xray.XrayService.get_active_node', AsyncMock(return_value=SimpleNamespace(id=node_id, name='test'))):
                target = await WarpService.target(db)
                assert target['port'] == 40001
                assert target['node_id'] == node_id


@pytest.mark.asyncio
async def test_unavailable_node_control_does_not_save_ipv4(auth_headers):
    async with TestingSessionLocal() as db:
        db.add(Node(name='test', host='203.0.113.4', port=443, protocol='trojan', secret='test-secret-long', is_enabled=True))
        await db.commit()
    with patch.object(WarpService, 'control_node', AsyncMock(side_effect=OSError())), patch('app.api.warp.ClientService.sync_xray_clients') as sync:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.put('/api/v1/warp/ipv4', headers=auth_headers, json={'ipv4_only': False})
        assert response.status_code == 409
        sync.assert_not_called()
    async with TestingSessionLocal() as db:
        assert await db.get(Setting, 'warp.ipv4_only') is None


@pytest.mark.asyncio
async def test_rotate_is_authenticated_and_requires_active_node():
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/v1/warp/rotate-ip')
        assert response.status_code == 401


def helper():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'warp-node-control.py'
    spec = importlib.util.spec_from_file_location('warp_node_controls_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rotation_reports_unchanged_address_without_deleting_registration():
    module = helper()
    result = {'ip': '104.28.1.2', 'country': 'DE', 'warp': 'on'}
    with patch.object(module, 'trace', return_value=result), patch.object(module, 'command') as command:
        assert module.rotate()['changed'] is False
        assert command.call_args_list[0].args[0] == ['systemctl', 'restart', 'warp-svc']
        assert all('registration' not in call.args[0] for call in command.call_args_list)


def test_rotation_failure_reconnects_existing_registration():
    module = helper()
    result = {'ip': '104.28.1.2', 'country': 'DE', 'warp': 'on'}
    with patch.object(module, 'trace', side_effect=[result, RuntimeError(), RuntimeError(), RuntimeError(), RuntimeError(), result]), patch.object(module, 'command') as command, patch.object(module.time, 'sleep'):
        with pytest.raises(RuntimeError):
            module.rotate()
        assert command.call_args.args[0] == ['systemctl', 'restart', 'warp-svc']


def test_trace_rejects_nonwarp_and_wrong_address_family():
    module = helper()
    for output in ('ip=104.28.1.2\nwarp=off', 'ip=2001:db8::1\nwarp=on'):
        with patch.object(module, 'command', return_value=output), pytest.raises(RuntimeError):
            module.trace()
