import asyncio
import datetime as dt
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.node_invite import NodeInvite
from app.services.watchdog import WatchdogService
from app.api import nodes


def competing_write(path):
    with sqlite3.connect(path, timeout=0.1) as db:
        db.execute("INSERT INTO settings(key,value) VALUES ('stability.competing','1')")


@pytest.mark.asyncio
async def test_watchdog_network_wait_does_not_hold_sqlite_writer():
    async with AsyncSessionLocal() as db:
        db.add_all([Node(name=str(i),host=f'192.0.2.{i}',port=443,
                         protocol='trojan',secret='test',status='healthy',priority=i)
                    for i in (1,2)])
        await db.commit()
    checks=0
    async with AsyncSessionLocal() as db:
        path=db.bind.url.database
        async def ping(host,port):
            nonlocal checks
            checks+=1
            if checks==2:
                await asyncio.to_thread(competing_write,path)
            return True,10
        service=WatchdogService()
        with patch.object(service,'_check_node_ping',side_effect=ping), \
             patch.object(service,'_probe_node_egress',return_value=None), \
             patch.object(service,'_update_country',new_callable=AsyncMock):
            await service._update_all_nodes_ping(db)
        assert checks==2
        await db.commit()


@pytest.mark.asyncio
async def test_registration_waits_route_lock_before_any_database_write(monkeypatch):
    token='stability-invite'
    async with AsyncSessionLocal() as db:
        db.add(NodeInvite(name='test',token_hash=nodes._hash_token(token),
                          expires_at=dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)))
        await db.commit()
    from app.services import cluster
    lock=asyncio.Lock()
    monkeypatch.setattr(cluster,'_route_lock',lock)
    async with AsyncSessionLocal() as db:
        path=db.bind.url.database
        await lock.acquire()
        with patch.object(cluster,'_apply_active_node',return_value=(True,'ok')) as apply, \
             patch.object(nodes.XrayService,'get_active_node',return_value=None):
            task=asyncio.create_task(nodes.register_node(
                nodes.NodeRegister(token=token,host='192.0.2.55',port=443),db))
            try:
                await asyncio.sleep(0.02)
                assert not task.done()
                await asyncio.to_thread(competing_write,path)
                lock.release()
                result=await asyncio.wait_for(task,3)
                assert result['status']=='connected'
                apply.assert_awaited_once()
            finally:
                if lock.locked():lock.release()
                if not task.done():
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):await task


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['sync','commit'])
async def test_toggle_restores_runtime_on_sync_or_commit_failure(failure):
    async with AsyncSessionLocal() as db:
        node=Node(name='test',host='192.0.2.77',port=443,protocol='trojan',secret='test',status='healthy')
        db.add(node);await db.commit();identifier=node.id
        with patch.object(nodes.ClientService,'sync_xray_clients',return_value=(failure!='sync','MTProto apply failed')), \
             patch.object(nodes.ClientService,'restore_committed_configs',new_callable=AsyncMock) as restore:
            if failure=='commit':
                with patch.object(db,'commit',side_effect=RuntimeError('commit failed')):
                    with pytest.raises(RuntimeError,match='commit failed'):
                        await nodes._toggle_node_enabled(identifier,db,None)
            else:
                with pytest.raises(HTTPException) as error:
                    await nodes._toggle_node_enabled(identifier,db,None)
                assert error.value.status_code==502
            restore.assert_awaited_once_with(db)
        assert (await db.get(Node,identifier)).is_enabled is True
