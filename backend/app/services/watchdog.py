import asyncio
import datetime
import logging
import os
import time
from typing import Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.services.cluster import apply_active_node, get_failover_settings, get_selected_node, is_manual_direct_route
from app.services.xray import DEFAULT_PROBE_URL, probe_enabled, probe_port_for_node
from app.bot.bot import get_bot
from app.bot.handlers import notify_admin
from app.services.events import log_event

logger = logging.getLogger(__name__)


class WatchdogService:
    """Проверяет состояние узлов и выполняет настроенное резервирование кластера."""

    def __init__(self):
        self.interval = max(5, int(os.getenv("FAILOVER_INTERVAL_S", os.getenv("WATCHDOG_INTERVAL", "15"))))
        self.failure_threshold = max(1, int(os.getenv("FAILOVER_FAILURE_COUNT", os.getenv("WATCHDOG_FAILURE_THRESHOLD", "3"))))
        self.recovery_threshold = max(1, int(os.getenv("FAILOVER_FAILBACK_STABLE_CHECKS", os.getenv("WATCHDOG_RECOVERY_THRESHOLD", "3"))))
        self.recovery_cooldown = max(0, int(os.getenv("FAILOVER_COOLDOWN_S", os.getenv("WATCHDOG_RECOVERY_COOLDOWN", "30"))))
        self.consecutive_failures: dict[int, int] = {}
        self.consecutive_successes: dict[int, int] = {}
        self.last_switch_time = 0.0
        self.country_checked: dict[int, float] = {}
        self.is_running = False
        self._task = None
        self.probe_url = os.getenv("XRAY_PROBE_URL", DEFAULT_PROBE_URL)
        self.probe_timeout = max(2.0, float(os.getenv("NODE_PROBE_TIMEOUT", "8")))

    async def _check_node_ping(self, host: str, port: int) -> Tuple[bool, int]:
        start = time.monotonic()
        writer = None
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
            return True, int((time.monotonic() - start) * 1000)
        except Exception:
            return False, 0
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _probe_node_egress(self, node: Node) -> Optional[Tuple[bool, int]]:
        """Проверяет реальный выход в интернет через конкретную ноду.

        Открытый порт ноды ничего не доказывает: нода может принимать соединение и при этом
        не иметь доступа в сеть или не резолвить домены. Запрос идёт через персональный
        SOCKS-инбунд, жёстко закреплённый за этой нодой, поэтому проверяется весь путь
        Trojan -> gRPC -> интернет ноды вместе с её DNS.

        Возвращает None, если диагностика выключена: тогда работает только проверка порта.
        """
        if not probe_enabled():
            return None
        proxy = f"socks5://127.0.0.1:{probe_port_for_node(node.id)}"
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=self.probe_timeout, follow_redirects=False) as client:
                response = await client.get(self.probe_url)
                return response.status_code < 500, int((time.monotonic() - start) * 1000)
        except Exception as exc:
            logger.debug("cluster.probe.failed id=%s host=%s reason=%s", node.id, node.host, exc)
            return False, 0

    async def _update_country(self, node: Node):
        """Не влияет на оценку здоровья ноды; запрос не чаще раза в шесть часов."""
        if not probe_enabled() or time.monotonic() < self.country_checked.get(node.id, 0):
            return
        self.country_checked[node.id] = time.monotonic() + 21600
        try:
            async with httpx.AsyncClient(proxy=f"socks5://127.0.0.1:{probe_port_for_node(node.id)}", timeout=3, trust_env=False) as client:
                response = await client.get("https://www.cloudflare.com/cdn-cgi/trace")
                response.raise_for_status()
            values = dict(line.split("=", 1) for line in response.text.splitlines() if "=" in line)
            code = values.get("loc", "").upper()
            if len(code) == 2 and code.isascii() and code.isalpha():
                node.country_code = code
        except Exception:
            # Недоступность геоданных не должна включать резервирование.
            self.country_checked[node.id] = time.monotonic() + 300

    async def _update_all_nodes_ping(self, session: AsyncSession, settings: Optional[dict] = None):
        settings = settings or await get_failover_settings(session)
        self.interval = settings["interval_s"]
        self.failure_threshold = settings["failure_count"]
        self.recovery_threshold = settings["failback_stable_checks"]
        self.recovery_cooldown = settings["cooldown_s"]
        nodes = (await session.execute(select(Node).order_by(Node.priority, Node.id))).scalars().all()
        now = datetime.datetime.now(datetime.timezone.utc)
        for node in nodes:
            if not node.is_enabled:
                node.is_active = False
                node.status = "disabled"
                self.consecutive_failures[node.id] = 0
                self.consecutive_successes[node.id] = 0
                continue

            connected, ping_ms = await self._check_node_ping(node.host, node.port)
            node.ping_ms = ping_ms
            probe = await self._probe_node_egress(node) if connected else None
            egress_ok = probe[0] if probe is not None else True
            # Диагностика не подменяет задержку: порог ping_threshold_ms отвечает только
            # за доступность порта, а работоспособность выхода определяется отдельно.
            bad = not (connected and egress_ok) or ping_ms > settings["ping_threshold_ms"]
            if bad:
                self.consecutive_failures[node.id] = self.consecutive_failures.get(node.id, 0) + 1
                self.consecutive_successes[node.id] = 0
                if self.consecutive_failures[node.id] >= settings["failure_count"]:
                    was_healthy = node.status == "healthy"
                    node.status = "unhealthy"
                    node.is_active = False
                    if was_healthy:
                        logger.warning("cluster.node.unhealthy id=%s ping_ms=%s connected=%s egress=%s", node.id, ping_ms, connected, egress_ok if probe is not None else "skip")
                        log_event("warning", "node", "Узел стал недоступен", {"node_id": node.id, "name": node.name, "ping_ms": ping_ms, "connected": connected, "egress_ok": egress_ok if probe is not None else None})
                        if settings["mode"] == "manual":
                            try:
                                await notify_admin(get_bot(), f"Нода {node.name} недоступна или превысила порог задержки ({ping_ms} мс). Автопереключение отключено.", notification_type="node_down")
                            except Exception:
                                logger.exception("Не удалось сообщить о недоступном узле")
            else:
                was_unhealthy = node.status != "healthy"
                node.last_seen = now
                self.consecutive_successes[node.id] = self.consecutive_successes.get(node.id, 0) + 1
                self.consecutive_failures[node.id] = 0
                node.status = "healthy"
                node.is_active = True
                await self._update_country(node)
                if was_unhealthy:
                    log_event("info", "node", "Узел снова доступен", {"node_id": node.id, "name": node.name, "ping_ms": ping_ms})
        await session.flush()

    async def _switch(self, session: AsyncSession, node: Optional[Node], reason: str) -> bool:
        current = await get_selected_node(session)
        if (current.id if current else None) == (node.id if node else None):
            return False
        try:
            ok, detail = await apply_active_node(session, node, source="auto")
        except Exception:
            await session.rollback()
            logger.exception("Ошибка автоматического переключения кластера: целевой узел=%s", getattr(node, "id", None))
            return False
        if not ok:
            await session.rollback()
            logger.error("Ошибка автоматического переключения кластера: целевой узел=%s, причина=%s", getattr(node, "id", None), detail)
            return False
        self.last_switch_time = time.monotonic()
        logger.warning("Автоматическое переключение кластера: причина=%s, прежний узел=%s, новый узел=%s", reason, getattr(current, "id", None), getattr(node, "id", None))
        target = f"{node.name} ({node.host})" if node else "прямой выход с мастер-сервера"
        log_event("warning" if reason != "failback" else "info", "cluster", "Автоматический failback выполнен" if reason == "failback" else "Автоматический failover выполнен", {"from_node_id": getattr(current, "id", None), "node_id": getattr(node, "id", None), "reason": reason})
        try:
            await notify_admin(get_bot(), f"Автоматическое переключение Xray: {target}.", notification_type="failover")
        except Exception:
            logger.exception("Не удалось сообщить об изменении маршрута кластера")
        return True

    async def _failover(self, session: AsyncSession, current_node_id: int, current_node_name: str = ""):
        settings = await get_failover_settings(session)
        current = await session.get(Node, current_node_id)
        if current and current.status != "unhealthy":
            current.status = "unhealthy"
            current.is_active = False
            await session.flush()
        healthy = (await session.execute(
            select(Node).where(Node.is_enabled.is_(True), Node.is_active.is_(True), Node.status == "healthy")
            .order_by(Node.priority, Node.id)
        )).scalars().all()
        candidate = next((node for node in healthy if node.id != current_node_id), None)
        if candidate:
            await self._switch(session, candidate, "node_unhealthy")
        elif settings["fallback_action"] == "direct":
            await self._switch(session, None, "all_nodes_unhealthy")
        else:
            logger.error("Все узлы недоступны; сохранён текущий маршрут")

    async def _failback(self, session: AsyncSession, primary_node: Node):
        await self._switch(session, primary_node, "failback")

    async def _check_cycle(self, session: AsyncSession):
        settings = await get_failover_settings(session)
        await self._update_all_nodes_ping(session, settings)
        await session.commit()
        selected = await get_selected_node(session)
        manual_direct = await is_manual_direct_route(session)

        if settings["mode"] == "auto" and selected is None and not manual_direct:
            first_healthy = (await session.execute(
                select(Node).where(Node.is_enabled.is_(True), Node.is_active.is_(True), Node.status == "healthy")
                .order_by(Node.priority, Node.id)
            )).scalars().first()
            if first_healthy:
                await self._switch(session, first_healthy, "route_unset")
                selected = await get_selected_node(session)

        if selected and (not selected.is_enabled or selected.status != "healthy"):
            if settings["mode"] == "manual":
                logger.warning("cluster.manual_mode.selected_node_unhealthy id=%s", selected.id)
                return
            await self._failover(session, selected.id, selected.name)
            selected = await get_selected_node(session)

        if settings["mode"] != "auto" or not settings["failback"] or manual_direct:
            return
        primary = (await session.execute(
            select(Node).where(Node.is_enabled.is_(True)).order_by(Node.priority, Node.id)
        )).scalars().first()
        if primary and primary.id != (selected.id if selected else None):
            stable = self.consecutive_successes.get(primary.id, 0) >= settings["failback_stable_checks"]
            cooldown = time.monotonic() - self.last_switch_time >= settings["cooldown_s"]
            if primary.status == "healthy" and stable and cooldown:
                await self._failback(session, primary)

    async def _loop(self):
        while self.is_running:
            try:
                async with AsyncSessionLocal() as session:
                    await self._check_cycle(session)
                    settings = await get_failover_settings(session)
                    self.interval = settings["interval_s"]
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка цикла наблюдения за узлами")
            await asyncio.sleep(self.interval)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Служба наблюдения за узлами запущена")

    async def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("Служба наблюдения за узлами остановлена")


watchdog = WatchdogService()
