from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from app.main import app
from app.schemas.node import NodeHosting
from app.models.node import Node
from conftest import TestingSessionLocal
from test_subadmins import account


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///etc/passwd', '//host.example', 'https://user:secret@host.example', 'https://host.example/\nsecret'])
def test_only_safe_http_links_are_accepted(url):
    with pytest.raises(ValidationError):
        NodeHosting(hosting_url=url)


@pytest.mark.asyncio
async def test_link_follows_invitation_and_can_be_changed_without_vpn_reconfiguration(auth_headers, monkeypatch):
    monkeypatch.setattr('app.api.nodes.ClientService.sync_xray_clients', AsyncMock(return_value=(True, '')))
    monkeypatch.setattr('app.api.nodes.get_failover_settings', AsyncMock(return_value={'mode': 'manual'}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
        created = await api.post('/api/v1/nodes/invites', headers=auth_headers, json={'name': 'Польша', 'hosting_url': 'https://host.example/servers/42'})
        assert created.status_code == 200, created.text
        token = created.json()['token']
        registered = await api.post('/api/v1/nodes/register', json={'token': token, 'host': '192.0.2.88', 'port': 443})
        assert registered.status_code == 201, registered.text
        assert 'hosting_url' not in registered.json()
        node_id = registered.json()['node_id']
        node = (await api.get(f'/api/v1/nodes/{node_id}', headers=auth_headers)).json()
        assert node['hosting_url'] == 'https://host.example/servers/42'
        again = (await api.post('/api/v1/nodes/invites', headers=auth_headers, json={'name': 'Переустановка'})).json()
        repeated = await api.post('/api/v1/nodes/register', json={'token': again['token'], 'host': '192.0.2.88', 'port': 443})
        assert repeated.status_code == 201 and repeated.json()['node_id'] == node_id
        assert (await api.get(f'/api/v1/nodes/{node_id}', headers=auth_headers)).json()['hosting_url'] == 'https://host.example/servers/42'
        sync = AsyncMock()
        monkeypatch.setattr('app.api.nodes.ClientService.sync_xray_clients', sync)
        updated = await api.patch(f'/api/v1/nodes/{node_id}/hosting', headers=auth_headers, json={'hosting_url': 'https://new.example'})
        assert updated.status_code == 200 and updated.json()['hosting_url'] == 'https://new.example/'
        cleared = await api.patch(f'/api/v1/nodes/{node_id}/hosting', headers=auth_headers, json={'hosting_url': ''})
        assert cleared.status_code == 200 and cleared.json()['hosting_url'] is None
        sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_subadmin_cannot_read_or_modify_hosting_links(auth_headers):
    _, sub = await account(auth_headers)
    async with TestingSessionLocal() as db:
        node = Node(name='Test', host='192.0.2.89', port=443, protocol='trojan', hosting_url='https://private-host.example', is_active=True, is_enabled=True, status='healthy', ping_ms=0)
        db.add(node)
        await db.commit()
        node_id = node.id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as api:
        public = await api.get(f'/api/v1/nodes/{node_id}', headers=sub)
        assert public.status_code == 200 and 'hosting_url' not in public.json()
        assert (await api.patch(f'/api/v1/nodes/{node_id}/hosting', headers=sub, json={'hosting_url': ''})).status_code == 403
        assert (await api.post('/api/v1/nodes/invites', headers=sub, json={'hosting_url': 'https://example.com'})).status_code == 403
