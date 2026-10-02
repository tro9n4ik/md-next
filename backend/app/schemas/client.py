import re
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional
from datetime import datetime

EMAIL_REGEX = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

class ClientBase(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    protocol: str # 'vless' or 'awg'

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        if v is None:
            raise ValueError("Имя обязательно для заполнения")
        v = v.strip()
        if not (1 <= len(v) <= 64):
            raise ValueError("Имя должно быть от 1 до 64 символов")
        return v

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: Optional[str]) -> str:
        if not v:
            return ""
        v = v.strip()
        if not v:
            return ""
        if not EMAIL_REGEX.match(v):
            raise ValueError("Некорректный формат email")
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> str:
        if not v:
            return ""
        return v.strip()

class ClientCreate(ClientBase):
    pass

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not (1 <= len(v) <= 64):
            raise ValueError("Имя должно быть от 1 до 64 символов")
        return v

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return ""
        if not EMAIL_REGEX.match(v):
            raise ValueError("Некорректный формат email")
        return v

    @field_validator('phone')
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return v.strip()

class ClientResponse(ClientBase):
    id: int
    uuid: Optional[str] = None
    public_key: Optional[str] = None
    traffic_used: int
    traffic_limit: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ClientLinkResponse(BaseModel):
    client: ClientResponse
    link: Optional[str] = None
    conf: Optional[str] = None
