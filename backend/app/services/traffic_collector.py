import asyncio
import logging
import datetime
import psutil
import json
from sqlalchemy import select
from sqlalchemy import delete
from app.db.database import AsyncSessionLocal
from app.models.traffic import TrafficSample
from app.services.net_utils import get_primary_interface
from app.models.client import Client, ClientProfile
from app.services.client_service import ClientService
from app.services.awg import AWGService
from app.services.events import log_event
from app.services.shell import run_cmd
from app.services.telegram_settings import get_telegram_settings_from_db
from app.bot.bot import bot_manager
from app.services.client_limits import refresh_period, subscription_block_reason, cdn_quota_exhausted

logger = logging.getLogger(__name__)

LAST_COUNTERS = None
AWG_LAST_COUNTERS: dict[str, tuple[int, int]] = {}
LIMITS_SYNC_PENDING = False


async def _run_command(*args: str) -> str:
    code, stdout, stderr = await run_cmd(*args, timeout=15)
    if code:
        raise RuntimeError(stderr or f"Команда {args[0]} завершилась с ошибкой")
    return stdout

async def _collect_client_traffic() -> None:
    global AWG_LAST_COUNTERS, LIMITS_SYNC_PENDING
    xray_deltas: dict[str, tuple[int, int]] = {}
    try:
        output = await _run_command("xray", "api", "statsquery", "-s", "127.0.0.1:10085", "-pattern", "user>>>", "-reset")
        payload = json.loads(output or "{}")
        for stat in payload.get("stat", []):
            name = stat.get("name", "")
            parts = name.split(">>>")
            if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
                up, down = xray_deltas.get(parts[1], (0, 0))
                amount = max(0, int(stat.get("value", 0)))
                xray_deltas[parts[1]] = (amount if parts[3] == "uplink" else up, amount if parts[3] == "downlink" else down)
    except Exception as exc:
        logger.warning("Ошибка сбора трафика клиентов Xray: %s", exc)

    awg_deltas: dict[str, tuple[int, int]] = {}
    try:
        output = await _run_command("awg", "show", "awg0", "transfer")
        current: dict[str, tuple[int, int]] = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                try:
                    current[parts[0]] = (int(parts[1]), int(parts[2]))
                except ValueError:
                    continue
        for public_key, (rx, tx) in current.items():
            old_rx, old_tx = AWG_LAST_COUNTERS.get(public_key, (rx, tx))
            awg_deltas[public_key] = (rx - old_rx if rx >= old_rx else rx, tx - old_tx if tx >= old_tx else tx)
        AWG_LAST_COUNTERS = current
    except Exception as exc:
        logger.warning("Ошибка сбора трафика клиентов AmneziaWG: %s", exc)

    quota_clients: list[tuple[int, str]] = []
    now = datetime.datetime.now(datetime.timezone.utc)
    async with AsyncSessionLocal() as session:
        clients = (await session.execute(select(Client))).scalars().all()
        for client in clients:
            refresh_period(client, now)
        result = await session.execute(select(ClientProfile, Client).join(Client, Client.id == ClientProfile.client_id))
        for profile, client in result.all():
            up = down = 0
            if profile.kind == "awg" and profile.public_key in awg_deltas:
                up, down = awg_deltas[profile.public_key]
                profile.traffic_up = (profile.traffic_up or 0) + up
                profile.traffic_down = (profile.traffic_down or 0) + down
            elif profile.kind != "awg":
                key = f"c{client.id}-{profile.kind}@md-next"
                if key in xray_deltas:
                    up, down = xray_deltas[key]
                if profile.kind == "vless_xhttp_tls":
                    cdn_up, cdn_down = xray_deltas.get(f"c{client.id}-cdn@md-next", (0, 0))
                    client.cdn_monthly_traffic_up = (client.cdn_monthly_traffic_up or 0) + cdn_up
                    client.cdn_monthly_traffic_down = (client.cdn_monthly_traffic_down or 0) + cdn_down
                    client.cdn_traffic_up = (client.cdn_traffic_up or 0) + cdn_up
                    client.cdn_traffic_down = (client.cdn_traffic_down or 0) + cdn_down
                    up += cdn_up
                    down += cdn_down
                profile.traffic_up = (profile.traffic_up or 0) + up
                profile.traffic_down = (profile.traffic_down or 0) + down
            client.monthly_traffic_up = (client.monthly_traffic_up or 0) + up
            client.monthly_traffic_down = (client.monthly_traffic_down or 0) + down
        profs = (await session.execute(select(ClientProfile))).scalars().all()
        totals: dict[int, int] = {}
        for profile in profs:
            totals[profile.client_id] = totals.get(profile.client_id, 0) + (profile.traffic_up or 0) + (profile.traffic_down or 0)
        for client in clients:
            client.traffic_total = totals.get(client.id, 0)
            client.traffic_used = client.traffic_total
            if client.is_active and client.traffic_limit and client.traffic_total >= client.traffic_limit:
                client.is_active = False
                quota_clients.append((client.id, client.name))
                logger.warning("Клиент %s (идентификатор=%s) превысил лимит трафика", client.name, client.id)
                log_event("warning", "traffic", "Клиент отключён из-за превышения лимита трафика", {"client_id": client.id, "name": client.name})
            if bool(client.access_blocked) != bool(subscription_block_reason(client, now)):
                LIMITS_SYNC_PENDING = True
            if bool(client.cdn_access_blocked) != cdn_quota_exhausted(client, now):
                LIMITS_SYNC_PENDING = True
        await session.commit()
        if quota_clients or LIMITS_SYNC_PENDING:
            LIMITS_SYNC_PENDING = True
            # При неудаче сохраняем счётчики, но повторяем применение в следующем
            # цикле. Ручное отключение клиента не отменяется обновлением месяца.
            xray_ok, xray_reason = await ClientService.sync_xray_clients(session)
            awg_ok, awg_reason = await AWGService.sync_server_config(session)
            if xray_ok and awg_ok:
                for client in clients:
                    blocked = bool(subscription_block_reason(client, now))
                    if bool(client.access_blocked) != blocked:
                        log_event("info", "client", "Доступ приостановлен по условиям подписки" if blocked else "Доступ возобновлён после обновления подписки", {"client_id": client.id, "reason": subscription_block_reason(client, now)})
                    client.access_blocked = blocked
                    cdn_blocked = cdn_quota_exhausted(client, now)
                    if bool(client.cdn_access_blocked) != cdn_blocked:
                        log_event("info", "client", "Обход БС приостановлен: месячный лимит исчерпан" if cdn_blocked else "Обход БС возобновлён после обновления лимита", {"client_id": client.id})
                    client.cdn_access_blocked = cdn_blocked
                await session.commit()
                LIMITS_SYNC_PENDING = False
            else:
                logger.error("Условия подписок не применены; повтор через минуту: Xray=%s, AmneziaWG=%s", xray_reason, awg_reason)

    if quota_clients and bot_manager.bot:
        tg_settings = await get_telegram_settings_from_db()
        if tg_settings.get("notify_quota", True) and tg_settings.get("admin_id"):
            for client_id, name in quota_clients:
                try:
                    await bot_manager.bot.send_message(tg_settings["admin_id"], f"Клиент {name} (ID {client_id}) отключён: превышен лимит трафика.")
                except Exception as exc:
                    logger.warning("Не удалось отправить уведомление о лимите: %s", exc)

async def _collect_traffic_sample():
    global LAST_COUNTERS

    iface = get_primary_interface()
    pernic = psutil.net_io_counters(pernic=True)
    current = pernic.get(iface)
    if not current:
        if pernic:
            current = list(pernic.values())[0]
        else:
            return

    now = datetime.datetime.now(datetime.timezone.utc)

    if LAST_COUNTERS is None:
        LAST_COUNTERS = current
        return

    rx_diff = current.bytes_recv - LAST_COUNTERS.bytes_recv
    tx_diff = current.bytes_sent - LAST_COUNTERS.bytes_sent
    LAST_COUNTERS = current

    if rx_diff < 0 or tx_diff < 0:
        rx_diff = max(0, current.bytes_recv)
        tx_diff = max(0, current.bytes_sent)

    try:
        async with AsyncSessionLocal() as session:
            sample = TrafficSample(
                timestamp=now,
                rx_bytes=rx_diff,
                tx_bytes=tx_diff
            )
            session.add(sample)

            cutoff = now - datetime.timedelta(days=400)
            await session.execute(delete(TrafficSample).where(TrafficSample.timestamp < cutoff))
            await session.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения трафика в collector: {e}")

async def start_traffic_collector():
    while True:
        try:
            await _collect_traffic_sample()
            await _collect_client_traffic()
        except Exception as e:
            logger.error(f"Ошибка в цикле сбора трафика: {e}")
        await asyncio.sleep(60)
