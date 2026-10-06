import pytest
import time
import unittest.mock as mock
from sqlalchemy.future import select
from app.models.node import Node
from app.models.setting import Setting
from app.db.database import AsyncSessionLocal
from app.services.watchdog import WatchdogService
from app.services.cluster import get_selected_node
from app.services.xray import XrayService

@pytest.mark.asyncio
async def test_watchdog_manual_disabled_node_remains_disabled():
    async with AsyncSessionLocal() as session:
        node = Node(
            name="Disabled-Node",
            host="127.0.0.1",
            port=443,
            protocol="trojan",
            secret="sec123",
            is_enabled=False,
            is_active=False,
            status="disabled"
        )
        session.add(node)
        await session.commit()
        node_id = node.id

    watchdog = WatchdogService()
    with mock.patch.object(watchdog, "_check_node_ping", return_value=(True, 10)):
        async with AsyncSessionLocal() as session:
            await watchdog._update_all_nodes_ping(session)

            res = await session.get(Node, node_id)
            assert res.is_enabled is False
            assert res.is_active is False
            assert res.status == "disabled"

@pytest.mark.asyncio
async def test_watchdog_failover_and_recovery_hysteresis():
    async with AsyncSessionLocal() as session:
        node_primary = Node(
            name="Primary-Node",
            host="1.1.1.1",
            port=443,
            protocol="trojan",
            secret="sec1",
            is_enabled=True,
            is_active=True,
            status="healthy"
        )
        node_backup = Node(
            name="Backup-Node",
            host="2.2.2.2",
            port=443,
            protocol="trojan",
            secret="sec2",
            is_enabled=True,
            is_active=True,
            status="healthy"
        )
        session.add(node_primary)
        session.add(node_backup)
        await session.flush()

        setting = Setting(key="active_node_id", value=str(node_primary.id))
        session.add(setting)
        await session.commit()
        p_id = node_primary.id
        b_id = node_backup.id

    watchdog = WatchdogService()
    watchdog.recovery_cooldown = 0

    async def mock_ping_fail(host, port):
        if host == "1.1.1.1":
            return False, 0
        return True, 10

    with mock.patch.object(watchdog, "_check_node_ping", side_effect=mock_ping_fail):
        with mock.patch("app.services.client_service.ClientService.sync_xray_clients", return_value=(True, "OK")):
            async with AsyncSessionLocal() as session:
                for _ in range(3):
                    await watchdog._update_all_nodes_ping(session)

                res_p = await session.get(Node, p_id)
                assert res_p.status == "unhealthy"

                await watchdog._failover(session, p_id, "Primary-Node")

                s_res = await session.execute(select(Setting).where(Setting.key == "active_node_id"))
                st = s_res.scalar_one_or_none()
                print("SETTING VALUE AFTER FAILOVER:", st.value if st else None)

                n_res = await session.execute(select(Node).where(Node.id == b_id))
                n_b = n_res.scalar_one_or_none()
                print("BACKUP NODE AFTER FAILOVER:", n_b.id if n_b else None, "is_active:", getattr(n_b, 'is_active', None), "status:", getattr(n_b, 'status', None))

                active_after = await get_selected_node(session)
                assert active_after is not None
                assert active_after.id == b_id

    async def mock_ping_recovered(host, port):
        return True, 15

    with mock.patch.object(watchdog, "_check_node_ping", side_effect=mock_ping_recovered):
        with mock.patch("app.services.client_service.ClientService.sync_xray_clients", return_value=(True, "OK")):
            async with AsyncSessionLocal() as session:
                for _ in range(3):
                    await watchdog._update_all_nodes_ping(session)

                res_p_rec = await session.get(Node, p_id)
                assert res_p_rec.status == "healthy"

                await watchdog._failback(session, res_p_rec)

                active_final = await get_selected_node(session)
                assert active_final is not None
                assert active_final.id == p_id
