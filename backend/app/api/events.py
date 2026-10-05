from datetime import datetime
from typing import Literal, Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.event import Event

router = APIRouter(prefix="/api/v1/events", tags=["События"], dependencies=[Depends(get_current_user)])


class EventResponse(BaseModel):
    id: int
    ts: datetime
    level: str
    category: str
    message: str
    meta: dict | None

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[EventResponse])
async def get_events(
    limit: int = Query(default=50, ge=1, le=500),
    level: Literal["info", "warning", "error"] | None = None,
    category: str | None = Query(default=None, max_length=64),
    since: datetime | None = None,
    until: datetime | None = None,
    client_id: int | None = None,
    node_id: int | None = None,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(Event)
    if level:
        query = query.where(Event.level == level)
    if category:
        query = query.where(Event.category == category)
    if since:
        query = query.where(Event.ts >= since)
    if until:
        query = query.where(Event.ts <= until)
    if client_id is not None:
        query = query.where(Event.meta['client_id'].as_integer() == client_id)
    if node_id is not None:
        from sqlalchemy import or_
        query = query.where(or_(Event.meta['node_id'].as_integer() == node_id, Event.meta['from_node_id'].as_integer() == node_id))
    query = query.order_by(Event.ts.desc(), Event.id.desc()).offset(offset).limit(limit)
    return (await db.execute(query)).scalars().all()
