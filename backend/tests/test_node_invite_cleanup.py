"""Очистка истории приглашений не затрагивает действующие токены и ноды."""
import datetime
import hashlib
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.node import Node
from app.models.node_invite import NodeInvite
from conftest import TestingSessionLocal
from test_subadmins import account


def invitation(name, **kwargs):
    return NodeInvite(name=name, token_hash=hashlib.sha256(name.encode()).hexdigest(), **kwargs)


@pytest.mark.asyncio
async def test_cleanup_preserves_live_invites_and_connected_nodes(auth_headers):
    now = datetime.datetime.now(datetime.timezone.utc)
    async with TestingSessionLocal() as db:
        node = Node(name='Existing', host='192.0.2.50', port=443, protocol='trojan', secret='synthetic-node-secret', is_enabled=True)
        db.add(node)
        await db.flush()
        node_id = node.id
        db.add_all([
            invitation('live', expires_at=now + datetime.timedelta(hours=2), revoked=False),
            invitation('used', expires_at=now + datetime.timedelta(hours=2), used_at=now, revoked=False, node_id=node_id),
            invitation('revoked', expires_at=now + datetime.timedelta(hours=2), revoked=True),
            invitation('expired', expires_at=now - datetime.timedelta(minutes=1), revoked=False),
            invitation('all-finished', expires_at=now - datetime.timedelta(minutes=1), used_at=now, revoked=True),
        ])
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        with patch('app.api.nodes.ClientService.sync_xray_clients', new=AsyncMock()) as sync:
            response = await api.delete('/api/v1/nodes/invites/history', headers=auth_headers)
            assert response.status_code == 200 and response.json() == {'deleted': 4}
            sync.assert_not_awaited()
        rows = (await api.get('/api/v1/nodes/invites', headers=auth_headers)).json()
        assert [r['name'] for r in rows] == ['live']
        assert all('token_hash' not in r and 'token' not in r for r in rows)
        # Живую команду можно скачать и после очистки; удалённые токены не оживают.
        assert (await api.get('/api/v1/nodes/join', params={'token': 'live'})).status_code == 200
        for token in ('used', 'revoked', 'expired', 'all-finished'):
            assert (await api.get('/api/v1/nodes/join', params={'token': token})).status_code == 401
            result = await api.post('/api/v1/nodes/register', json={'token': token, 'host': '192.0.2.51', 'port': 443})
            assert result.status_code == 401
        repeat = await api.delete('/api/v1/nodes/invites/history', headers=auth_headers)
        assert repeat.status_code == 200 and repeat.json() == {'deleted': 0}
    async with TestingSessionLocal() as db:
        stored = await db.get(Node, node_id)
        assert stored and stored.secret == 'synthetic-node-secret' and stored.is_enabled
        assert len((await db.execute(select(Node))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_cleanup_requires_administrator_even_with_subscription_deletion_right(auth_headers):
    _, headers = await account(auth_headers, can_delete_subscriptions=True)
    async with TestingSessionLocal() as db:
        db.add(invitation('revoked', expires_at=datetime.datetime.now(datetime.timezone.utc), revoked=True))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        assert (await api.delete('/api/v1/nodes/invites/history')).status_code == 401
        assert (await api.delete('/api/v1/nodes/invites/history', headers=headers)).status_code == 403
    async with TestingSessionLocal() as db:
        assert len((await db.execute(select(NodeInvite))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_empty_history_can_be_cleared_repeatedly(auth_headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        for _ in range(2):
            response = await api.delete('/api/v1/nodes/invites/history', headers=auth_headers)
            assert response.status_code == 200 and response.json() == {'deleted': 0}
