from datetime import datetime
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class NodeHosting(BaseModel):
    hosting_url: str | None = Field(default=None, max_length=2048)

    @field_validator('hosting_url')
    @classmethod
    def valid_hosting_url(cls, value):
        if value is None or not value.strip():
            return None
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError('Ссылка не должна содержать управляющие символы')
        value = value.strip()
        if value.startswith('//') or re.match(r'(?i)^(?:javascript|data|file|vbscript):', value) or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', value):
            try:
                url = HttpUrl(value)
            except ValueError:
                raise ValueError('Укажите название хостера или ссылку HTTP/HTTPS.') from None
        else:
            return value
        if url.username or url.password:
            raise ValueError('Укажите ссылку без логина и пароля')
        result = str(url)
        if len(result) > 2048:
            raise ValueError('Ссылка слишком длинная')
        return result


class NodePublicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    country: str | None = Field(default=None, validation_alias="country_code")
    status: str
    is_online: bool
    last_seen: datetime | None = None


class NodeAdminResponse(NodePublicResponse):
    hosting_url: str | None = None
    transport: Literal["xray", "mdnt"] = "xray"
    host: str
    port: int
    protocol: str
    country_code: str | None = None
    is_active: bool
    is_enabled: bool = True
    ping_ms: int
    priority: int = 0
