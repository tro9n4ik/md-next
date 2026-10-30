"""Прямой маршрут приостанавливает применение WARP, сохраняя выбор пользователя."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting
from app.models.routing import RoutingRule
from app.services.client_service import ClientService
from app.services.cluster import apply_active_node
from app.services.warp import WarpService
from app.services.xray import XrayService


async def seed(db):
    node=Node(name='Выход',host='192.0.2.10',port=443,protocol='trojan',secret='test',is_enabled=True,is_active=True)
    db.add(node);await db.flush()
    db.add_all([Setting(key='active_node_id',value=str(node.id)),Setting(key='warp.usage',value='rules'),
                Setting(key='warp.node_id',value=str(node.id)),
                RoutingRule(domain_or_ip='domain:example.com',action='warp',description='Сохранённый пресет',is_active=True)])
    await db.commit()
    return node


def assert_direct(call):
    options=call.kwargs['profile_options']
    assert options['warp_usage']=='off'
    assert all(rule.get('outboundTag')!='warp' for rule in options['routing_rules'])
    config=json.loads(XrayService.generate_config([],active_node=call.kwargs['active_node'],profile_options=options))
    assert config['outbounds'][0]['tag']=='direct'
    assert not any(outbound['tag']=='warp' for outbound in config['outbounds'])


@pytest.mark.asyncio
async def test_direct_then_restart_then_node_restores_warp_without_changing_presets():
    async with AsyncSessionLocal() as db:
        node=await seed(db)
        with patch('app.services.client_service.apply_reality_sni',AsyncMock()), \
             patch.object(XrayService,'apply_config',AsyncMock(return_value=(True,'ok'))) as apply, \
             patch('app.services.mtproto.sync_config',AsyncMock(return_value=(True,'ok'))), \
             patch.object(WarpService,'test_target',AsyncMock(return_value={'warp':'on','country':'DE'})), \
             patch.object(WarpService,'control_node',AsyncMock()):
            assert (await apply_active_node(db,None))[0]
            assert (await db.get(Setting,'active_node_id')).value=='direct:manual'
            assert_direct(apply.call_args)
            assert (await db.get(Setting,'warp.usage')).value=='rules'
            # Обычная синхронизация после перезапуска читает сохранённый direct.
            assert (await ClientService.sync_xray_clients(db))[0]
            assert_direct(apply.call_args)
            state=await WarpService.status(db)
            assert state['paused'] and not state['enabled'] and state['requested_enabled']
            assert (await apply_active_node(db,node))[0]
            options=apply.call_args.kwargs['profile_options']
            assert options['warp_usage']=='rules' and options['warp_node_id']==node.id
            assert any(rule.get('outboundTag')=='warp' for rule in options['routing_rules'])
            state=await WarpService.status(db)
            assert state['enabled'] and not state['paused']


@pytest.mark.asyncio
async def test_failed_direct_switch_keeps_old_node_and_warp():
    async with AsyncSessionLocal() as db:
        node=await seed(db);identifier=node.id
        with patch('app.services.client_service.apply_reality_sni',AsyncMock()), \
             patch.object(XrayService,'apply_config',AsyncMock(return_value=(False,'ошибка применения'))):
            assert not (await apply_active_node(db,None))[0]
        assert (await db.get(Setting,'active_node_id')).value==str(identifier)
        assert (await db.get(Setting,'warp.usage')).value=='rules'


@pytest.mark.asyncio
@pytest.mark.parametrize('route',['direct:manual','direct:auto'])
async def test_stored_direct_also_pauses_legacy_all_warp(route):
    async with AsyncSessionLocal() as db:
        db.add_all([Setting(key='active_node_id',value=route),Setting(key='warp.usage',value='all')]);await db.commit()
        with patch('app.services.client_service.apply_reality_sni',AsyncMock()), \
             patch.object(XrayService,'apply_config',AsyncMock(return_value=(True,'ok'))) as apply, \
             patch('app.services.mtproto.sync_config',AsyncMock(return_value=(True,'ok'))):
            assert (await ClientService.sync_xray_clients(db))[0]
        assert_direct(apply.call_args)
        assert (await db.get(Setting,'warp.usage')).value=='all'


@pytest.mark.asyncio
async def test_missing_node_without_explicit_direct_still_rejects_warp():
    async with AsyncSessionLocal() as db:
        db.add(Setting(key='warp.usage',value='rules'));await db.commit()
        with patch('app.services.client_service.apply_reality_sni',AsyncMock()), \
             patch.object(XrayService,'apply_config',AsyncMock()) as apply:
            ok,reason=await ClientService.sync_xray_clients(db)
        assert not ok and 'активную ноду' in reason
        apply.assert_not_awaited()
