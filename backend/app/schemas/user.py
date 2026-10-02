from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    totp_required: bool = False

class TOTPSetupResponse(BaseModel):
    secret: str
    otpauth_url: str

class TOTPVerifyRequest(BaseModel):
    code: str

class UserResponse(BaseModel):
    id: int
    username: str
    totp_enabled: bool
