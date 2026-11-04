from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NodePublicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    country: str | None = Field(default=None, validation_alias="country_code")
    status: str
    is_online: bool
    last_seen: datetime | None = None


class NodeAdminResponse(NodePublicResponse):
    host: str
    port: int
    protocol: str
    country_code: str | None = None
    is_active: bool
    is_enabled: bool = True
    ping_ms: int
    priority: int = 0
