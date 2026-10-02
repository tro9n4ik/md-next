"""Срок подписки и месячные периоды с привязкой к дате создания клиента."""

from calendar import monthrange
from datetime import datetime, timedelta, timezone

from app.models.client import Client


def utc(value: datetime) -> datetime:
    # SQLite возвращает даты без часового пояса; в базе храним UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def add_months(anchor: datetime, months: int) -> datetime:
    anchor = utc(anchor)
    ordinal = anchor.year * 12 + anchor.month - 1 + months
    year, month = divmod(ordinal, 12)
    month += 1
    return anchor.replace(year=year, month=month, day=min(anchor.day, monthrange(year, month)[1]))


def traffic_period(client: Client, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = utc(now or datetime.now(timezone.utc))
    anchor = utc(client.created_at or now)
    months = max(0, (now.year - anchor.year) * 12 + now.month - anchor.month)
    if add_months(anchor, months) > now:
        months = max(0, months - 1)
    return add_months(anchor, months), add_months(anchor, months + 1)


def monthly_usage(client: Client, now: datetime | None = None) -> tuple[int, int]:
    start, _ = traffic_period(client, now)
    saved = client.traffic_period_start
    if saved is None or utc(saved) != start:
        return 0, 0
    return int(client.monthly_traffic_up or 0), int(client.monthly_traffic_down or 0)


def refresh_period(client: Client, now: datetime | None = None) -> None:
    start, _ = traffic_period(client, now)
    if client.traffic_period_start is None or utc(client.traffic_period_start) != start:
        client.traffic_period_start = start
        client.monthly_traffic_up = client.monthly_traffic_down = 0


def subscription_block_reason(client: Client, now: datetime | None = None) -> str | None:
    now = utc(now or datetime.now(timezone.utc))
    if client.expires_at is not None and utc(client.expires_at) <= now:
        return "expired"
    up, down = monthly_usage(client, now)
    if client.monthly_traffic_limit and up + down >= client.monthly_traffic_limit:
        return "monthly_quota"
    return None


def access_allowed(client: Client, now: datetime | None = None) -> bool:
    return bool(client.is_active and not subscription_block_reason(client, now))


def limit_info(client: Client, now: datetime | None = None) -> dict:
    now = utc(now or datetime.now(timezone.utc))
    start, end = traffic_period(client, now)
    up, down = monthly_usage(client, now)
    return {"expires_at": utc(client.expires_at) if client.expires_at else None,
            "monthly_traffic_limit": client.monthly_traffic_limit or 0,
            "monthly_traffic_up": up, "monthly_traffic_down": down,
            "monthly_traffic_used": up + down,
            "traffic_period_start": start, "traffic_period_end": end,
            "access_allowed": access_allowed(client, now),
            "blocked_reason": "disabled" if not client.is_active else subscription_block_reason(client, now)}


def expiry_for_period(period: str, custom: datetime | None, now: datetime, previous: datetime | None = None) -> datetime | None:
    now = utc(now)
    if period == "unlimited":
        return None
    if period == "custom":
        if custom is None or custom.tzinfo is None:
            raise ValueError("Укажите дату окончания подписки с часовым поясом")
        custom = utc(custom)
        if custom <= now:
            raise ValueError("Дата окончания подписки должна быть в будущем")
        return custom
    anchor = max(now, utc(previous)) if previous else now
    if period == "week":
        return anchor + timedelta(days=7)
    return add_months(anchor, 1 if period == "month" else 12)
