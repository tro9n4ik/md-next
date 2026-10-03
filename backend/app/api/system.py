import asyncio
import os
import time
import socket
import datetime
import psutil
import pathlib
import time as monotonic_time
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.traffic import TrafficSample
from app.api.auth import get_current_user
from app.services.net_utils import get_primary_interface
from app.models.setting import Setting
from app.services.warp import WarpService
from app.services.shell import run_cmd
from app.bot.bot import bot_manager

router = APIRouter(prefix="/api/v1/system", tags=["Система"])

LAST_NET_IO = None
LAST_NET_TIME = None

def _get_psutil_stats():
    global LAST_NET_IO, LAST_NET_TIME

    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)

    try:
        load_1, load_5, load_15 = os.getloadavg()
    except (AttributeError, OSError):
        load_1, load_5, load_15 = 0.0, 0.0, 0.0

    iface = get_primary_interface()
    pernic = psutil.net_io_counters(pernic=True)
    net_io = pernic.get(iface)
    if not net_io and pernic:
        net_io = list(pernic.values())[0]

    now = time.time()

    rx_speed = 0.0
    tx_speed = 0.0

    if LAST_NET_IO is not None and LAST_NET_TIME is not None and net_io is not None:
        dt = now - LAST_NET_TIME
        if dt > 0:
            rx_diff = net_io.bytes_recv - LAST_NET_IO.bytes_recv
            tx_diff = net_io.bytes_sent - LAST_NET_IO.bytes_sent
            if rx_diff >= 0:
                rx_speed = rx_diff / dt
            if tx_diff >= 0:
                tx_speed = tx_diff / dt

    LAST_NET_IO = net_io
    LAST_NET_TIME = now

    return {
        "cpu_percent": cpu_percent,
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "free": mem.free,
            "percent": mem.percent
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent
        },
        "uptime": uptime_seconds,
        "loadavg": [round(load_1, 2), round(load_5, 2), round(load_15, 2)],
        "net_speed": {
            "rx_bytes_per_sec": rx_speed,
            "tx_bytes_per_sec": tx_speed
        }
    }

@router.get("/info")
async def get_system_info():
    version_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "VERSION"))
    version = "2.2.0"
    if os.path.exists(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                version = f.read().strip()
        except Exception:
            pass

    return {
        "hostname": socket.gethostname(),
        "version": version
    }

@router.get("/stats", dependencies=[Depends(get_current_user)])
async def get_system_stats():
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(None, _get_psutil_stats)
    return stats

@router.get("/traffic", dependencies=[Depends(get_current_user)])
async def get_system_traffic(db: AsyncSession = Depends(get_db)):
    now = datetime.datetime.now(datetime.timezone.utc)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_30d = now - datetime.timedelta(days=30)

    # За сегодня
    q_today = select(
        func.coalesce(func.sum(TrafficSample.rx_bytes), 0),
        func.coalesce(func.sum(TrafficSample.tx_bytes), 0)
    ).where(TrafficSample.timestamp >= start_today)
    res_today = (await db.execute(q_today)).first()
    rx_today, tx_today = res_today[0], res_today[1]

    # За 30 дней
    q_30d = select(
        func.coalesce(func.sum(TrafficSample.rx_bytes), 0),
        func.coalesce(func.sum(TrafficSample.tx_bytes), 0)
    ).where(TrafficSample.timestamp >= start_30d)
    res_30d = (await db.execute(q_30d)).first()
    rx_30d, tx_30d = res_30d[0], res_30d[1]

    # За всё время
    q_all = select(
        func.coalesce(func.sum(TrafficSample.rx_bytes), 0),
        func.coalesce(func.sum(TrafficSample.tx_bytes), 0)
    )
    res_all = (await db.execute(q_all)).first()
    rx_all, tx_all = res_all[0], res_all[1]

    return {
        "today": {
            "rx": rx_today,
            "tx": tx_today,
            "total": rx_today + tx_today
        },
        "last_30_days": {
            "rx": rx_30d,
            "tx": tx_30d,
            "total": rx_30d + tx_30d
        },
        "all_time": {
            "rx": rx_all,
            "tx": tx_all,
            "total": rx_all + tx_all
        }
    }


async def _service_active(service: str) -> tuple[bool, str]:
    try:
        code, stdout, stderr = await run_cmd("systemctl", "is-active", service, timeout=3)
        detail = stdout.strip() or stderr.strip() or "Служба не активна"
        return code == 0 and detail == "active", detail
    except RuntimeError as exc:
        return False, str(exc)


async def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.5)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _certificate_days() -> int | None:
    host = os.getenv("SERVER_HOST", "")
    path = os.getenv("TLS_CERT_PATH") or (f"/etc/letsencrypt/live/{host}/fullchain.pem" if host else "")
    if not path:
        return None
    try:
        pem = await asyncio.to_thread(pathlib.Path(path).read_bytes)
        cert = x509.load_pem_x509_certificate(pem, default_backend())
        expires = getattr(cert, "not_valid_after_utc", cert.not_valid_after.replace(tzinfo=datetime.timezone.utc))
        return (expires - datetime.datetime.now(datetime.timezone.utc)).days
    except Exception:
        return None


@router.get("/health", dependencies=[Depends(get_current_user)])
async def get_system_health(db: AsyncSession = Depends(get_db)):
    checks: list[dict] = [{"key": "api", "name": "Панель (API)", "status": "ok", "description": "API отвечает на запросы"}]

    started = monotonic_time.perf_counter()
    try:
        await asyncio.wait_for(db.execute(select(func.now())), timeout=3)
        db_ms = round((monotonic_time.perf_counter() - started) * 1000)
        checks.append({"key": "database", "name": "База данных", "status": "warning" if db_ms > 500 else "ok", "description": f"Ответ за {db_ms} мс"})
    except Exception as exc:
        checks.append({"key": "database", "name": "База данных", "status": "error", "description": f"Ошибка запроса: {type(exc).__name__}"})

    xray_running, _ = await _service_active("xray")
    xray_port = await _port_listening(8444)
    checks.append({"key": "xray", "name": "Xray", "status": "ok" if xray_running and xray_port else "error", "description": f"Служба {'работает' if xray_running else 'остановлена'}; порт 8444 {'слушается' if xray_port else 'не слушается'}"})

    try:
        interfaces = {name for _, name in socket.if_nameindex()}
    except OSError:
        try:
            interfaces = set(os.listdir("/sys/class/net"))
        except OSError:
            interfaces = set()
    awg_config = os.getenv("AWG_CONFIG_PATH", "/etc/amnezia/amneziawg/awg0.conf")
    if "awg0" not in interfaces and not os.path.exists(awg_config):
        awg_status, awg_description = "not_configured", "Интерфейс awg0 не настроен"
    elif "awg0" in interfaces:
        awg_status, awg_description = "ok", "Интерфейс awg0 поднят"
    else:
        awg_status, awg_description = "warning", "Конфигурация есть, интерфейс awg0 не поднят"
    checks.append({"key": "awg", "name": "AmneziaWG", "status": awg_status, "description": awg_description})

    nginx_running, nginx_detail = await _service_active("nginx")
    checks.append({"key": "nginx", "name": "Nginx", "status": "ok" if nginx_running else "error", "description": "Служба работает" if nginx_running else f"Служба не работает ({nginx_detail})"})

    if bot_manager.status == "running":
        telegram_status, telegram_description = "ok", "Бот работает"
    elif bot_manager.status == "error":
        telegram_status, telegram_description = "warning", f"Ошибка бота: {str(bot_manager.last_error or 'неизвестная причина')[:180]}"
    else:
        telegram_status, telegram_description = "disabled", "Бот выключен"
    checks.append({"key": "telegram", "name": "Telegram-бот", "status": telegram_status, "description": telegram_description})

    usage_setting = await db.get(Setting, "warp.usage")
    if not usage_setting or usage_setting.value == "off":
        checks.append({"key": "warp", "name": "WARP", "status": "disabled", "description": "Использование WARP в Xray выключено"})
    else:
        try:
            state = await asyncio.wait_for(WarpService.status(db), timeout=8)
            connected = state.get("installed") and (state.get("remote") or state.get("service_active")) and state.get("state") == "Connected"
            description = (f"Подключён через {state.get('name', 'ноду')}" if state.get("remote") else "Подключён") if connected else "Не подключён"
            checks.append({"key": "warp", "name": "WARP", "status": "ok" if connected else "warning", "description": description})
        except Exception as exc:
            checks.append({"key": "warp", "name": "WARP", "status": "warning", "description": f"Статус недоступен ({type(exc).__name__})"})

    cert_days = await _certificate_days()
    if cert_days is None:
        cert_status, cert_description = "not_configured", "Не удалось прочитать сертификат"
    elif cert_days < 0:
        cert_status, cert_description = "error", "Срок действия сертификата истёк"
    else:
        cert_status = "warning" if cert_days <= 14 else "ok"
        cert_description = f"Истекает через {cert_days} дн."
    checks.append({"key": "certificate", "name": "Сертификат", "status": cert_status, "description": cert_description})

    overall = "error" if any(check["status"] == "error" for check in checks) else "warning" if any(check["status"] == "warning" for check in checks) else "ok"
    return {"status": overall, "checks": checks}
