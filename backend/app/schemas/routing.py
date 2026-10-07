from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Optional, Literal
from datetime import datetime
from app.services.input_validation import routing_match_type

class RoutingRuleBase(BaseModel):
    domain_or_ip: str = Field(min_length=1, max_length=512)
    target_node_id: Optional[int] = Field(default=None, gt=0)
    action: Literal['proxy', 'direct', 'block', 'warp'] = "proxy"
    description: Optional[str] = None
    is_active: bool = True

    @field_validator('domain_or_ip')
    @classmethod
    def valid_rule(cls, value: str) -> str:
        routing_match_type(value)
        return value.strip()

class RoutingRuleCreate(RoutingRuleBase):
    pass

class RoutingRuleResponse(RoutingRuleBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
