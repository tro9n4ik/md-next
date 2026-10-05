import json
from typing import Literal
from pydantic import BaseModel, Field
from app.models.setting import Setting


class SubscriptionTemplate(BaseModel):
    id: str = Field(pattern=r'^[a-z0-9_-]{1,24}$')
    name: str = Field(min_length=1, max_length=64)
    period: Literal['week','month','year','unlimited'] = 'month'
    monthly_traffic_limit: int = Field(default=0, ge=0, le=9007199254740991)


DEFAULTS = [SubscriptionTemplate(id='month', name='Месяц · 100 ГБ', monthly_traffic_limit=100*1024**3),
            SubscriptionTemplate(id='year', name='Год · без лимита', period='year')]


async def get_templates(db):
    row = await db.get(Setting, 'subscription.templates')
    try:
        return [SubscriptionTemplate(**v) for v in json.loads(row.value)] if row else DEFAULTS
    except (ValueError, TypeError):
        return DEFAULTS
