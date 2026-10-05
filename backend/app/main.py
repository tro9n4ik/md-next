import asyncio
import os
import logging
import psutil
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.sql import text

from app.db.database import engine, AsyncSessionLocal
from app.api.clients import router as clients_router, subscription_router
from app.api.nodes import router as nodes_router
from app.api.cluster import router as cluster_router
from app.api.warp import router as warp_router
from app.api.auth import router as auth_router
from app.api.routing import router as routing_router
from app.api.system import router as system_router
from app.api.settings import router as settings_router
from app.api.protocols import router as protocols_router
from app.api.dns import router as dns_router
from app.api.events import router as events_router
from app.api.cdn import router as cdn_router
from app.api.operations import router as operations_router
from app.services.watchdog import watchdog
from app.services.traffic_collector import start_traffic_collector
from app.services.telegram_settings import get_telegram_settings_from_db, resolve_telegram_proxy
from app.bot import bot_manager
from app.services.client_service import ClientService
from app.services.reality_keys import ensure_reality_key_pair
from app.services.awg import AWGService
from app.services.events import event_cleanup_loop, flush_pending_events, log_event
from app.services.shell import find_command

logger = logging.getLogger(__name__)

def _get_version() -> str:
    version_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "VERSION"))
    if os.path.exists(version_file):
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "2.2.0"

@asynccontextmanager
async def lifespan(app: FastAPI):
    jwt_secret = os.getenv("JWT_SECRET_KEY")
    if not jwt_secret:
        raise RuntimeError("КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения JWT_SECRET_KEY обязательна и не задана!")

    # Прогрев первичного вызова cpu_percent
    psutil.cpu_percent(interval=None)

    required_commands = {
        "xray": "Xray",
        "systemctl": "systemctl",
        "nginx": "Nginx",
        "awg": "AmneziaWG (awg)",
        "awg-quick": "AmneziaWG (awg-quick)",
        "warp-cli": "WARP (warp-cli)",
    }
    for command, name in required_commands.items():
        if find_command(command) is None:
            warning = f"Не найдена команда {name} в PATH; связанные функции панели будут недоступны."
            logger.warning(warning)
            log_event("warning", "service", warning, {"command": command})

    event_cleanup_task = asyncio.create_task(event_cleanup_loop())
    log_event("info", "service", "Сервис панели запущен")
    try:
        async with AsyncSessionLocal() as session:
            # Пара ключей Reality может остаться рассинхронизированной в базе:
            # тогда все Reality-ссылки не проходят handshake, а панель не
            # показывает ошибок. Приводим пару к верной до применения конфига.
            await ensure_reality_key_pair(session)
            xray_ok, xray_message = await ClientService.sync_xray_clients(session)
            if not xray_ok:
                logger.error("Не удалось применить профили клиентов Xray при запуске: %s", xray_message)
            awg_ok, awg_message = await AWGService.sync_server_config(session)
            if not awg_ok:
                logger.error("Не удалось применить профили AmneziaWG при запуске: %s", awg_message)
    except Exception:
        logger.exception("Не удалось синхронизировать профили клиентов при запуске")
    # Проверкам узлов нужны отдельные входы, созданные при первичной синхронизации.
    watchdog.start()
    traffic_task = asyncio.create_task(start_traffic_collector())
    from app.services.notifications import notification_loop
    notification_task = asyncio.create_task(notification_loop())
    app.state.traffic_task = traffic_task
    app.state.notification_task = notification_task

    try:
        tg_settings = await get_telegram_settings_from_db()
        if tg_settings.get("token"):
            async with AsyncSessionLocal() as session:
                proxy = await resolve_telegram_proxy(session, tg_settings)
            await bot_manager.start(tg_settings["token"], proxy)
    except Exception as e:
        bot_manager.status = "error"
        bot_manager.last_error = "Не удалось запустить бота. Проверьте выбранную ноду и настройки Telegram."

    yield

    log_event("info", "service", "Сервис панели остановлен")
    traffic_task = app.state.traffic_task
    notification_task = app.state.notification_task
    traffic_task.cancel()
    notification_task.cancel()
    try:
        await notification_task
    except asyncio.CancelledError:
        pass
    event_cleanup_task.cancel()
    try:
        await event_cleanup_task
    except asyncio.CancelledError:
        pass
    await flush_pending_events()
    await watchdog.stop()
    await bot_manager.stop()
    await engine.dispose()

app = FastAPI(
    title="MD-Next API",
    description="API для панели управления MD-Next (Xray Reality + AmneziaWG)",
    version=_get_version(),
    lifespan=lifespan
)


@app.middleware('http')
async def settings_audit(request, call_next):
    response = await call_next(request)
    groups = {'dns':'DNS','protocols':'Протоколы','routing':'Маршрутизация','cluster':'Кластер','warp':'WARP'}
    parts = request.url.path.strip('/').split('/')
    group = parts[2] if len(parts)>2 and parts[:2]==['api','v1'] else ''
    if request.method in ('PUT','POST','DELETE') and response.status_code<400 and group in groups:
        # Read-only probes do not count as settings changes. Never record request bodies.
        if not request.url.path.endswith(('/check','/test')):
            log_event('info','settings','Изменены настройки: '+groups[group],{'method':request.method,'path':request.url.path})
    return response

allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "")
allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()] or [
    "http://127.0.0.1:8443",
    "https://127.0.0.1:8443"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(system_router)
app.include_router(settings_router)
app.include_router(operations_router)
app.include_router(protocols_router)
app.include_router(dns_router)
app.include_router(clients_router)
app.include_router(subscription_router)
app.include_router(nodes_router)
app.include_router(cluster_router)
app.include_router(warp_router)
app.include_router(routing_router)
app.include_router(events_router)
app.include_router(cdn_router)

@app.get("/")
async def root():
    return {"status": "ok", "message": "MD-Next API работает"}

@app.get("/health")
async def health_check():
    try:
        async with AsyncSessionLocal() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=3.0)
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})
    return {"status": "ok"}
