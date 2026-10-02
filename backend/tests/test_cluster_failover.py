from unittest import mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.main import app
from app.models.node import Node
from app.models.setting import Setting
from app.services.watchdog import WatchdogService


async def _request(method, path, auth_headers, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=auth_headers, **kwargs)


async def _node(session, name, priority=0, *, enabled=True, active=True, status="healthy", host="127.0.0.1"):
    node = Node(name=name, host=host, port=443, protocol="trojan", secret="secret", priority=priority,
                is_enabled=enabled, is_active=active, status=status)
    session.add(node)
    await session.flush()
    return node


@pytest.mark.asyncio
async def test_manual_switch_validates_node_and_supports_direct_route(auth_headers):
    async with AsyncSessionLocal() as session:
        node = await _node(session, "exit-a")
        disabled = await _node(session, "exit-disabled", enabled=False, active=False, status="disabled")
        await session.commit()
        node_id, disabled_id = node.id, disabled.id

    with mock.patch("app.services.cluster.ClientService.sync_xray_clients", return_value=(True, "ok")):
        switched = await _request("PUT", "/api/v1/cluster/active-node", auth_headers, json={"node_id": node_id})
        assert switched.status_code == 200
        rejected = await _request("PUT", "/api/v1/cluster/active-node", auth_headers, json={"node_id": disabled_id})
        assert rejected.status_code == 409
        direct = await _request("PUT", "/api/v1/cluster/active-node", auth_headers, json={"node_id": None})
        assert direct.status_code == 200
        assert direct.json()["active_node_id"] is None


@pytest.mark.asyncio
async def test_manual_direct_route_is_not_replaced_by_auto_watchdog(auth_headers):
    async with AsyncSessionLocal() as session:
        node = await _node(session, "healthy-primary")
        session.add(Setting(key="failover.mode", value="auto"))
        node_id = node.id
        await session.commit()

    with mock.patch("app.services.cluster.ClientService.sync_xray_clients", return_value=(True, "ok")):
        response = await _request("PUT", "/api/v1/cluster/active-node", auth_headers, json={"node_id": None})
    assert response.status_code == 200

    watchdog = WatchdogService()
    with mock.patch.object(watchdog, "_check_node_ping", return_value=(True, 10)), \
         mock.patch("app.services.cluster.ClientService.sync_xray_clients", new=mock.AsyncMock(return_value=(True, "ok"))), \
         mock.patch("app.services.watchdog.notify_admin", new=mock.AsyncMock()):
        async with AsyncSessionLocal() as session:
            await watchdog._check_cycle(session)
            selected = await session.scalar(select(Setting).where(Setting.key == "active_node_id"))
            assert selected.value == "direct:manual"
            assert (await session.get(Node, node_id)).status == "healthy"


@pytest.mark.asyncio
async def test_route_check_reports_actual_egress_ip(auth_headers):
    with mock.patch("app.api.cluster.probe_current_exit", new=mock.AsyncMock(return_value={"ip": "203.0.113.7", "country": "RU", "warp": "off"})):
        response = await _request("POST", "/api/v1/cluster/route/check", auth_headers)
    assert response.status_code == 200
    assert response.json()["exit_ip"] == "203.0.113.7"
    assert response.json()["country"] == "RU"


@pytest.mark.asyncio
async def test_manual_switch_apply_failure_rolls_back_selected_node(auth_headers):
    async with AsyncSessionLocal() as session:
        current = await _node(session, "current")
        target = await _node(session, "target", priority=1)
        session.add(Setting(key="active_node_id", value=str(current.id)))
        target_id, current_id = target.id, current.id
        await session.commit()

    with mock.patch("app.services.cluster.ClientService.sync_xray_clients", return_value=(False, "xray failed")):
        response = await _request("PUT", "/api/v1/cluster/active-node", auth_headers, json={"node_id": target_id})
    assert response.status_code == 502
    async with AsyncSessionLocal() as session:
        selected = await session.scalar(select(Setting).where(Setting.key == "active_node_id"))
        assert selected.value == str(current_id)


@pytest.mark.asyncio
async def test_node_reorder_and_route_endpoint(auth_headers):
    async with AsyncSessionLocal() as session:
        first = await _node(session, "first", priority=0)
        second = await _node(session, "second", priority=1)
        first_id, second_id = first.id, second.id
        await session.commit()

    response = await _request("POST", "/api/v1/nodes/reorder", auth_headers, json={"ids": [second_id, first_id]})
    assert response.status_code == 200
    nodes = await _request("GET", "/api/v1/nodes", auth_headers)
    assert [node["id"] for node in nodes.json()] == [second_id, first_id]
    route = await _request("GET", "/api/v1/cluster/route", auth_headers)
    assert route.status_code == 200
    assert {"server", "vpn", "active_node", "next_candidate", "failover_mode"} <= route.json().keys()


@pytest.mark.asyncio
async def test_failover_settings_are_saved_without_restart(auth_headers):
    payload = {"mode": "manual", "ping_threshold_ms": 250, "failure_count": 2, "interval_s": 10,
               "failback": False, "failback_stable_checks": 4, "cooldown_s": 45, "fallback_action": "keep"}
    response = await _request("PUT", "/api/v1/cluster/failover", auth_headers, json=payload)
    assert response.status_code == 200
    assert response.json() == payload


@pytest.mark.asyncio
async def test_manual_watchdog_marks_latency_failure_without_switching():
    async with AsyncSessionLocal() as session:
        primary = await _node(session, "primary", priority=0, host="1.1.1.1")
        backup = await _node(session, "backup", priority=1, host="2.2.2.2")
        session.add_all([Setting(key="active_node_id", value=str(primary.id)), Setting(key="failover.mode", value="manual"),
                         Setting(key="failover.failure_count", value="1"), Setting(key="failover.ping_threshold_ms", value="300")])
        primary_id, backup_id = primary.id, backup.id
        await session.commit()

    watchdog = WatchdogService()
    async def ping(host, port):
        return True, 450 if host == "1.1.1.1" else 20
    with mock.patch.object(watchdog, "_check_node_ping", side_effect=ping), \
         mock.patch("app.services.watchdog.notify_admin", new=mock.AsyncMock()):
        async with AsyncSessionLocal() as session:
            await watchdog._check_cycle(session)
            selected = await session.scalar(select(Setting).where(Setting.key == "active_node_id"))
            assert selected.value == str(primary_id)
            assert (await session.get(Node, primary_id)).status == "unhealthy"
            assert (await session.get(Node, backup_id)).status == "healthy"


@pytest.mark.asyncio
async def test_failure_count_requires_consecutive_failed_checks():
    async with AsyncSessionLocal() as session:
        node = await _node(session, "unstable", host="3.3.3.3")
        session.add(Setting(key="failover.failure_count", value="2"))
        node_id = node.id
        await session.commit()

    watchdog = WatchdogService()
    with mock.patch.object(watchdog, "_check_node_ping", return_value=(False, 0)):
        async with AsyncSessionLocal() as session:
            await watchdog._update_all_nodes_ping(session)
            assert (await session.get(Node, node_id)).status == "healthy"
            await session.commit()
            await watchdog._update_all_nodes_ping(session)
            assert (await session.get(Node, node_id)).status == "unhealthy"


@pytest.mark.asyncio
async def test_auto_failover_failback_and_direct_when_all_nodes_fail():
    async with AsyncSessionLocal() as session:
        primary = await _node(session, "primary", priority=0, host="1.1.1.1")
        backup = await _node(session, "backup", priority=1, host="2.2.2.2")
        session.add_all([Setting(key="active_node_id", value=str(primary.id)), Setting(key="failover.mode", value="auto"),
                         Setting(key="failover.failure_count", value="1"), Setting(key="failover.ping_threshold_ms", value="300"),
                         Setting(key="failover.cooldown_s", value="0"), Setting(key="failover.failback_stable_checks", value="2"),
                         Setting(key="failover.fallback_action", value="direct")])
        primary_id, backup_id = primary.id, backup.id
        await session.commit()

    watchdog = WatchdogService()
    async def fail_primary(host, port):
        return (host != "1.1.1.1", 20 if host != "1.1.1.1" else 0)
    with mock.patch.object(watchdog, "_check_node_ping", side_effect=fail_primary), \
         mock.patch("app.services.cluster.ClientService.sync_xray_clients", new=mock.AsyncMock(return_value=(True, "ok"))), \
         mock.patch("app.services.watchdog.notify_admin", new=mock.AsyncMock()):
        async with AsyncSessionLocal() as session:
            await watchdog._check_cycle(session)
            selected = await session.scalar(select(Setting).where(Setting.key == "active_node_id"))
            assert selected.value == str(backup_id)

    async def all_good(host, port):
        return True, 20
    with mock.patch.object(watchdog, "_check_node_ping", side_effect=all_good), \
         mock.patch("app.services.cluster.ClientService.sync_xray_clients", new=mock.AsyncMock(return_value=(True, "ok"))), \
         mock.patch("app.services.watchdog.notify_admin", new=mock.AsyncMock()):
        async with AsyncSessionLocal() as session:
            await watchdog._check_cycle(session)
            await watchdog._check_cycle(session)
            selected = await session.scalar(select(Setting).where(Setting.key == "active_node_id"))
            assert selected.value == str(primary_id)

    async def all_down(host, port):
        return False, 0
    with mock.patch.object(watchdog, "_check_node_ping", side_effect=all_down), \
         mock.patch("app.services.cluster.ClientService.sync_xray_clients", new=mock.AsyncMock(return_value=(True, "ok"))), \
         mock.patch("app.services.watchdog.notify_admin", new=mock.AsyncMock()):
        async with AsyncSessionLocal() as session:
            await watchdog._check_cycle(session)
            selected = await session.scalar(select(Setting).where(Setting.key == "active_node_id"))
            assert selected.value == ""
