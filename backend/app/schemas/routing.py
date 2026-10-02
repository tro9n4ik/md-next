from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class RoutingRuleBase(BaseModel):
    domain_or_ip: str
    target_node_id: Optional[int] = None
    action: str = "proxy" # "proxy", "direct", "block"
    description: Optional[str] = None
    is_active: bool = True

class RoutingRuleCreate(RoutingRuleBase):
    pass

class RoutingRuleResponse(RoutingRuleBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
