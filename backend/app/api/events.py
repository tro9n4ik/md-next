from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.event import Event

router = APIRouter(prefix="/api/v1/events", tags=["Events"], dependencies=[Depends(get_current_user)])


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
    db: AsyncSession = Depends(get_db),
):
    query = select(Event)
    if level:
        query = query.where(Event.level == level)
    if category:
        query = query.where(Event.category == category)
    query = query.order_by(Event.ts.desc(), Event.id.desc()).limit(limit)
    return (await db.execute(query)).scalars().all()
