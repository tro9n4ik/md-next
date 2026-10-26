"""Administrator-managed, restricted accounts for issuing client profiles."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_password_hash, require_admin
from app.db.database import get_db
from app.models.user import User
from app.services.events import log_event

router = APIRouter(
    prefix="/api/v1/users",
    tags=["Субадминистраторы"],
    dependencies=[Depends(require_admin)],
)


def validate_password(value: str) -> str:
    if len(value) < 10 or len(value.encode("utf-8")) > 72:
        raise ValueError(
            "Пароль должен содержать минимум 10 символов и не более 72 байт"
        )
    return value


class SubadminCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str
    view_all_clients: bool = False

    @field_validator("password")
    @classmethod
    def password_is_valid(cls, value: str) -> str:
        return validate_password(value)


class SubadminUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool | None = None
    password: str | None = None
    view_all_clients: bool | None = None

    @field_validator("password")
    @classmethod
    def password_is_valid(cls, value: str | None) -> str | None:
        return validate_password(value) if value is not None else None


def account_data(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "totp_enabled": user.totp_enabled,
        "view_all_clients": user.view_all_clients,
    }


@router.get("")
async def list_subadmins(db: AsyncSession = Depends(get_db)):
    users = (
        (
            await db.execute(
                select(User).where(User.role == "subadmin").order_by(User.id)
            )
        )
        .scalars()
        .all()
    )
    return [account_data(user) for user in users]


@router.post("", status_code=201)
async def create_subadmin(
    request: SubadminCreate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    user = User(
        username=request.username,
        hashed_password=get_password_hash(request.password),
        role="subadmin",
        is_active=True,
        totp_enabled=False,
        token_version=1,
        view_all_clients=request.view_all_clients,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Этот логин уже занят") from None
    await db.refresh(user)
    log_event(
        "info",
        "users",
        "Создан субадминистратор",
        {"user_id": user.id, "username": user.username, "actor_id": actor.id},
    )
    return account_data(user)


@router.put("/{user_id}")
async def update_subadmin(
    user_id: int,
    request: SubadminUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
):
    user = await db.get(User, user_id)
    if user is None or user.role != "subadmin":
        raise HTTPException(404, "Субадминистратор не найден")
    changed = False
    if request.is_active is not None and user.is_active != request.is_active:
        user.is_active = request.is_active
        changed = True
    if request.password is not None:
        user.hashed_password = get_password_hash(request.password)
        changed = True
    if (
        request.view_all_clients is not None
        and user.view_all_clients != request.view_all_clients
    ):
        user.view_all_clients = request.view_all_clients
        changed = True
    if changed:
        # A disabled account must not regain old sessions after re-enabling.
        user.token_version = (user.token_version or 1) + 1
        await db.commit()
        await db.refresh(user)
        log_event(
            "info",
            "users",
            "Учётная запись субадминистратора изменена",
            {
                "user_id": user.id,
                "username": user.username,
                "is_active": user.is_active,
                "actor_id": actor.id,
            },
        )
    return account_data(user)
