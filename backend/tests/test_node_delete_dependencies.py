from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.node import Node
from app.models.routing import RoutingRule
from conftest import TestingSessionLocal


@pytest.mark.asyncio
@pytest.mark.parametrize('active', [True, False])
async def test_delete_preserves_node_referenced_by_proxy_rule(auth_headers, active):
    async with TestingSessionLocal() as db:
        node = Node(name='Incomplete', host='192.0.2.80', port=443, protocol='trojan',
                    secret=None, status='unhealthy')
        db.add(node)
        await db.flush()
        node_id = node.id
        rule = RoutingRule(domain_or_ip='example.com', target_node_id=node_id,
                           action='proxy', is_active=active)
        db.add(rule)
        await db.commit()
        rule_id = rule.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        with patch('app.api.nodes.ClientService.sync_xray_clients', new=AsyncMock(return_value=(True, 'ok'))) as sync:
            response = await api.delete(f'/api/v1/nodes/{node_id}', headers=auth_headers)
            assert response.status_code == 409
            assert f'№{rule_id}' in response.json()['detail']
            sync.assert_not_awaited()
            async with TestingSessionLocal() as db:
                assert await db.get(Node, node_id) is not None
                assert (await db.get(RoutingRule, rule_id)).target_node_id == node_id
                await db.delete(await db.get(RoutingRule, rule_id))
                await db.commit()
            response = await api.delete(f'/api/v1/nodes/{node_id}', headers=auth_headers)
            assert response.status_code == 204
            sync.assert_awaited_once()
    async with TestingSessionLocal() as db:
        assert await db.get(Node, node_id) is None
