import asyncio
import datetime as dt
import logging
import re
from typing import Any

from sqlalchemy import delete, select

from app.db import database
from app.models.event import Event

logger = logging.getLogger(__name__)
_pending: set[asyncio.Task] = set()
_SECRET_KEY = re.compile(r"password|token|secret|license|private.?key|api.?key|authorization|credential", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+|(?:password|token|secret|license|api[_ -]?key)\s*[:=]\s*)[^\s,;]+")


def _safe_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_meta(item) for key, item in value.items() if not _SECRET_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_safe_meta(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return _SECRET_VALUE.sub(r"\1[скрыто]", value[:1000]) if isinstance(value, str) else value
    return str(value)[:1000]


async def _persist(level: str, category: str, message: str, meta: dict | None) -> None:
    try:
        async with database.AsyncSessionLocal() as db:
            db.add(Event(level=level, category=category[:64], message=message[:512], meta=_safe_meta(meta or {})))
            await db.commit()
    except Exception:
        logger.exception("Не удалось записать событие в журнал")


def log_event(level: str, category: str, message: str, meta: dict | None = None) -> None:
    """Schedule event persistence without waiting on a separate database transaction."""
    try:
        normalized_level = level if level in {"info", "warning", "error"} else "info"
        task = asyncio.get_running_loop().create_task(_persist(normalized_level, category, message, meta))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception:
        # Event logging must never affect the request being handled.
        return


async def flush_pending_events() -> None:
    tasks = tuple(_pending)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def cleanup_events(db=None) -> tuple[int, int]:
    """Remove events older than 30 days and retain at most the newest 5000."""
    own_session = db is None
    session_context = database.AsyncSessionLocal() if own_session else None
    session = await session_context.__aenter__() if session_context else db
    try:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        old = await session.execute(delete(Event).where(Event.ts < cutoff))
        removed_old = max(0, old.rowcount or 0)
        excess_ids = (await session.execute(select(Event.id).order_by(Event.ts.desc(), Event.id.desc()).offset(5000))).scalars().all()
        if excess_ids:
            await session.execute(delete(Event).where(Event.id.in_(excess_ids)))
        await session.commit()
        return removed_old, len(excess_ids)
    except Exception:
        await session.rollback()
        raise
    finally:
        if session_context:
            await session_context.__aexit__(None, None, None)


async def event_cleanup_loop() -> None:
    while True:
        try:
            await cleanup_events()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Не удалось очистить журнал событий")
        await asyncio.sleep(6 * 60 * 60)
