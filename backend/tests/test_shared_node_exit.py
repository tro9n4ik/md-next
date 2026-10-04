from unittest.mock import AsyncMock, patch
import pytest
from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting
from app.models.routing import RoutingRule
from app.services.cluster import apply_active_node


async def seed(db):
    nodes = [Node(name=name, host="127.0.0.1", port=443, protocol="trojan", secret="test", is_enabled=True, is_active=True, status="healthy") for name in ("DE", "NL")]
    db.add_all(nodes)
    await db.flush()
    for key, value in {"active_node_id": str(nodes[0].id), "warp.node_id": str(nodes[0].id), "warp.usage": "rules", "telegram_use_node": "true", "telegram_node_id": str(nodes[0].id)}.items():
        db.add(Setting(key=key, value=value))
    rule = RoutingRule(domain_or_ip="domain:example.com", action="proxy", target_node_id=nodes[0].id, is_active=True)
    db.add(rule)
    await db.commit()
    return nodes, rule


@pytest.mark.asyncio
async def test_switch_moves_all_node_routes_together(monkeypatch):
    monkeypatch.setenv("NODE_PROBE_ENABLED", "true")
    async with AsyncSessionLocal() as db:
        nodes, rule = await seed(db)
        async def applied(session, active_node):
            assert active_node.id == nodes[1].id
            assert (await session.get(Setting, "warp.node_id")).value == str(nodes[1].id)
            assert (await session.get(Setting, "telegram_node_id")).value == str(nodes[1].id)
            assert rule.target_node_id == nodes[1].id
            return True, "ok"
        with patch("app.services.warp.WarpService.test_target", new=AsyncMock(return_value={"warp": "on", "country": "NL"})), patch("app.services.cluster.ClientService.sync_xray_clients", side_effect=applied), patch("app.bot.bot.bot_manager.rebind_proxy", new=AsyncMock()) as rebind:
            ok, _ = await apply_active_node(db, nodes[1])
        assert ok
        assert (await db.get(Setting, "active_node_id")).value == str(nodes[1].id)
        rebind.assert_awaited_once_with(f"socks5://127.0.0.1:{10900 + nodes[1].id}")


@pytest.mark.asyncio
@pytest.mark.parametrize("warp_ok", [True, False])
async def test_failed_switch_preserves_all_previous_settings(monkeypatch, warp_ok):
    monkeypatch.setenv("NODE_PROBE_ENABLED", "true")
    async with AsyncSessionLocal() as db:
        nodes, rule = await seed(db)
        previous_id, target_id, rule_id = nodes[0].id, nodes[1].id, rule.id
        with patch("app.services.warp.WarpService.test_target", new=AsyncMock(return_value={"warp": "on" if warp_ok else "off", "country": "NL"})), patch("app.services.cluster.ClientService.sync_xray_clients", return_value=(False, "test failure")), patch("app.bot.bot.bot_manager.rebind_proxy", new=AsyncMock()) as rebind:
            ok, _ = await apply_active_node(db, await db.get(Node, target_id))
        assert not ok
        for key in ("active_node_id", "warp.node_id", "telegram_node_id"):
            assert (await db.get(Setting, key)).value == str(previous_id)
        assert (await db.get(RoutingRule, rule_id)).target_node_id == previous_id
        rebind.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_transport_changes_without_restarting_its_handler():
    from app.bot.bot import BotManager
    manager = BotManager()
    manager.bot = manager.create_bot("123456:TESTTOKEN", "socks5://127.0.0.1:10901")
    bot = manager.bot
    previous = bot.session
    with patch.object(previous, "close", new=AsyncMock()) as close:
        await manager.rebind_proxy("socks5://127.0.0.1:10902")
    assert manager.bot is bot
    assert bot.session.proxy == "socks5://127.0.0.1:10902"
    close.assert_awaited_once()
    await bot.session.close()
