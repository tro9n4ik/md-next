import os
import time
import datetime
import jwt
import pyotp
from typing import Dict, Tuple
from pydantic import BaseModel
from passlib.context import CryptContext
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse, TOTPSetupResponse, TOTPVerifyRequest, UserResponse
from app.services.events import log_event

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

LOGIN_ATTEMPTS: Dict[Tuple[str, str], list] = {}
IP_ATTEMPTS: Dict[str, list] = {}

RATE_LIMIT_USER_ATTEMPTS = 5
RATE_LIMIT_IP_ATTEMPTS = 20
RATE_LIMIT_WINDOW_SECONDS = 15 * 60

router = APIRouter(prefix="/api/v1/auth", tags=["Auth & 2FA"])

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict) -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения JWT_SECRET_KEY не задана!")
    to_encode = data.copy()
    to_encode.setdefault("token_version", 1)
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, secret, algorithm=ALGORITHM)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("КРИТИЧЕСКАЯ ОШИБКА: Переменная окружения JWT_SECRET_KEY не задана!")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        token_version: int = payload.get("token_version")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен аутентификации")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен аутентификации")

    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")

    user_token_version = getattr(user, 'token_version', 1) or 1
    if token_version is None or token_version != user_token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Срок действия токена истёк или пароль был изменён"
        )

    return user

async def ensure_admin_created(db: AsyncSession):
    result = await db.execute(select(User).where(User.username == "admin"))
    user = result.scalar_one_or_none()
    if not user:
        initial_password = os.getenv("INITIAL_ADMIN_PASSWORD")
        if not initial_password or initial_password == "admin123456":
            raise RuntimeError("КРИТИЧЕСКАЯ ОШИБКА БЕЗОПАСНОСТИ: Укажите надёжный INITIAL_ADMIN_PASSWORD!")
        admin_user = User(
            username="admin",
            hashed_password=get_password_hash(initial_password),
            totp_enabled=False,
            token_version=1
        )
        db.add(admin_user)
        await db.commit()

def _cleanup_rate_limits(now: float):
    for key in list(LOGIN_ATTEMPTS.keys()):
        valid = [t for t in LOGIN_ATTEMPTS[key] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if valid:
            LOGIN_ATTEMPTS[key] = valid
        else:
            LOGIN_ATTEMPTS.pop(key, None)

    for ip in list(IP_ATTEMPTS.keys()):
        valid = [t for t in IP_ATTEMPTS[ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if valid:
            IP_ATTEMPTS[ip] = valid
        else:
            IP_ATTEMPTS.pop(ip, None)

def _check_rate_limit(client_ip: str, username: str):
    now = time.time()
    _cleanup_rate_limits(now)

    user_key = (client_ip, username)
    user_attempts = LOGIN_ATTEMPTS.get(user_key, [])
    if len(user_attempts) >= RATE_LIMIT_USER_ATTEMPTS:
        log_event("warning", "auth", "Вход временно заблокирован из-за частых попыток", {"username": username})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много неудачных попыток входа для аккаунта. Попробуйте через 15 минут."
        )

    ip_attempts = IP_ATTEMPTS.get(client_ip, [])
    if len(ip_attempts) >= RATE_LIMIT_IP_ATTEMPTS:
        log_event("warning", "auth", "Вход временно заблокирован для IP из-за частых попыток", {"username": username})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много неудачных попыток входа с Вашего IP. Попробуйте через 15 минут."
        )

def _record_failed_attempt(client_ip: str, username: str):
    now = time.time()
    user_key = (client_ip, username)
    LOGIN_ATTEMPTS.setdefault(user_key, []).append(now)
    IP_ATTEMPTS.setdefault(client_ip, []).append(now)

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    client_ip = request.headers.get("X-Real-IP") or (request.client.host if request.client else "127.0.0.1")
    _check_rate_limit(client_ip, req.username)

    await ensure_admin_created(db)
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.hashed_password):
        _record_failed_attempt(client_ip, req.username)
        log_event("warning", "auth", "Неудачная попытка входа", {"username": req.username})
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    if user.totp_enabled:
        if not req.totp_code:
            return TokenResponse(access_token="", totp_required=True)
        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(req.totp_code):
            _record_failed_attempt(client_ip, req.username)
            log_event("warning", "auth", "Неудачная проверка кода 2FA при входе", {"username": req.username})
            raise HTTPException(status_code=401, detail="Неверный код 2FA")

    LOGIN_ATTEMPTS.pop((client_ip, req.username), None)

    user_version = getattr(user, 'token_version', 1) or 1
    token = create_access_token({"sub": user.username, "token_version": user_version})
    log_event("info", "auth", "Вход в панель выполнен", {"username": user.username})
    return TokenResponse(access_token=token, totp_required=False)

class TOTPSetupRequest(BaseModel):
    current_code: str = None

@router.post("/2fa/setup", response_model=TOTPSetupResponse)
async def setup_2fa(
    req: TOTPSetupRequest = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    current_code = req.current_code if req else None

    if user.totp_enabled:
        if not current_code:
            raise HTTPException(
                status_code=400,
                detail="2FA уже включена. Для смены требуется действующий код 2FA."
            )
        totp = pyotp.TOTP(user.totp_secret)
        if not totp.verify(current_code):
            raise HTTPException(status_code=400, detail="Неверный код 2FA")

    secret = pyotp.random_base32()
    user.totp_pending_secret = secret
    await db.commit()
    log_event("info", "security", "Настройка 2FA начата", {"username": user.username})

    totp = pyotp.TOTP(secret)
    otpauth_url = totp.provisioning_uri(name=user.username, issuer_name="MD-Next VPN")
    return TOTPSetupResponse(secret=secret, otpauth_url=otpauth_url)

@router.post("/2fa/verify")
async def verify_2fa(req: TOTPVerifyRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    secret_to_verify = user.totp_pending_secret or user.totp_secret

    if not secret_to_verify:
        raise HTTPException(status_code=400, detail="2FA не инициализирована")

    totp = pyotp.TOTP(secret_to_verify)
    if not totp.verify(req.code):
        raise HTTPException(status_code=400, detail="Неверный одноразовый код")

    user.totp_secret = secret_to_verify
    user.totp_pending_secret = None
    user.totp_enabled = True
    await db.commit()
    log_event("info", "security", "Двухфакторная аутентификация включена", {"username": user.username})
    return {"status": "ok", "message": "2FA успешно подтверждена"}

class TOTPDisableRequest(BaseModel):
    current_code: str

@router.post("/2fa/disable")
async def disable_2fa(req: TOTPDisableRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA не включена")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(req.current_code):
        raise HTTPException(status_code=400, detail="Неверный код 2FA")

    user.totp_enabled = False
    user.totp_secret = None
    user.totp_pending_secret = None
    await db.commit()
    log_event("info", "security", "Двухфакторная аутентификация отключена", {"username": user.username})
    return {"status": "ok", "message": "2FA успешно отключена"}

@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, username=user.username, totp_enabled=user.totp_enabled)

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@router.put("/password")
async def change_password(
    req: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not verify_password(req.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный текущий пароль")

    if len(req.new_password) < 10:
        raise HTTPException(status_code=400, detail="Новый пароль должен состоять минимум из 10 символов")

    user.hashed_password = get_password_hash(req.new_password)
    user.token_version = (getattr(user, 'token_version', 1) or 1) + 1
    db.add(user)
    await db.commit()
    await db.refresh(user)
    log_event("info", "security", "Пароль администратора изменён", {"username": user.username})
    return {"status": "ok", "message": "Пароль администратора успешно изменён"}
