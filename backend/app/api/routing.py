import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.auth import get_current_user
from app.db.database import get_db
from app.models.node import Node
from app.models.routing import RoutingRule
from app.schemas.routing import RoutingRuleCreate, RoutingRuleResponse
from app.services.client_service import ClientService
from app.services.routing_rules import validate_rule_value

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/routing", tags=["Правила маршрутизации"], dependencies=[Depends(get_current_user)])


@router.get("/rules", response_model=List[RoutingRuleResponse])
async def get_routing_rules(db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(RoutingRule).order_by(RoutingRule.id))).scalars().all()


@router.post("/rules", response_model=RoutingRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_routing_rule(rule_data: RoutingRuleCreate, db: AsyncSession = Depends(get_db)):
    try:
        validate_rule_value(rule_data.domain_or_ip)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if rule_data.action not in ("proxy", "direct", "block", "warp"):
        raise HTTPException(status_code=400, detail="Допустимые действия: через ноду, напрямую, блокировать или через WARP")
    if rule_data.action == "proxy":
        if not rule_data.target_node_id:
            raise HTTPException(status_code=400, detail="Для действия «через ноду» выберите целевую ноду")
        node = (await db.execute(select(Node).where(Node.id == rule_data.target_node_id))).scalar_one_or_none()
        if not node:
            raise HTTPException(status_code=404, detail=f"Нода с ID {rule_data.target_node_id} не найдена")
        if not getattr(node, "is_enabled", True):
            raise HTTPException(status_code=400, detail=f"Нода «{node.name}» отключена")
    else:
        rule_data.target_node_id = None
    new_rule = RoutingRule(**rule_data.model_dump())
    db.add(new_rule)
    await db.commit()
    await db.refresh(new_rule)
    return new_rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_routing_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = (await db.execute(select(RoutingRule).where(RoutingRule.id == rule_id))).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    await db.delete(rule)
    await db.commit()


@router.post("/apply")
async def apply_routing_rules(db: AsyncSession = Depends(get_db)):
    success, message = await ClientService.sync_xray_clients(db)
    if not success:
        code = 400 if any(token in message.lower() for token in ("geosite.dat", "geoip.dat", "warp")) else 502
        logger.error("Не удалось применить маршрутизацию Xray основного сервера: %s", message)
        raise HTTPException(status_code=code, detail=f"Не удалось применить правила маршрутизации: {message}")
    return {"success": True, "message": "Правила маршрутизации применены к Xray основного сервера"}
