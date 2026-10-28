"""Общие группы; счётчики учитывают только доступных пользователю клиентов."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.auth import get_current_user, require_admin
from app.db.database import get_db
from app.models.client import Client, ClientGroup
from app.models.user import User
from app.services.events import log_event

router = APIRouter(prefix="/api/v1/client-groups", tags=["Группы клиентов"])


class GroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    color: Literal["emerald", "blue", "violet", "amber", "rose", "slate"] = "emerald"

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not 1 <= len(value) <= 48 or len(value.casefold()) > 48:
            raise ValueError("Название должно содержать от 1 до 48 символов")
        return value


def group_data(group: ClientGroup) -> dict:
    return {"id": group.id, "name": group.name, "color": group.color}


@router.get("")
async def list_groups(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    groups = (await db.execute(select(ClientGroup).order_by(ClientGroup.id))).scalars().all()
    query = select(Client.group_id, func.count(Client.id)).group_by(Client.group_id)
    if user.role != "admin" and not user.view_all_clients:
        query = query.where(Client.created_by_id == user.id)
    counts = dict((await db.execute(query)).all())
    return {"groups": [{**group_data(group), "count": counts.get(group.id, 0)} for group in groups],
            "ungrouped_count": counts.get(None, 0), "total": sum(counts.values())}


async def save_group(db: AsyncSession, group: ClientGroup, data: GroupInput):
    group.name, group.name_key, group.color = data.name, data.name.casefold(), data.color
    db.add(group)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Группа с таким названием уже существует") from exc
    return group_data(group)


@router.post("", status_code=201)
async def create_group(data: GroupInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    result = await save_group(db, ClientGroup(), data)
    log_event("info", "client", "Группа клиентов создана", {"group_id": result["id"], "actor_id": user.id})
    return result


@router.put("/{group_id}")
async def edit_group(group_id: int, data: GroupInput, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    group = await db.get(ClientGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    result = await save_group(db, group, data)
    log_event("info", "client", "Группа клиентов обновлена", {"group_id": group_id, "actor_id": user.id})
    return result


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(require_admin)):
    group = await db.get(ClientGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    await db.execute(update(Client).where(Client.group_id == group_id).values(group_id=None))
    await db.delete(group)
    await db.commit()
    log_event("info", "client", "Группа удалена; клиенты сохранены без группы", {"group_id": group_id, "actor_id": user.id})
