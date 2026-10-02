from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.database import Base

class NodeInvite(Base):
    """
    Модель одноразового приглашения (инвайта) для добавления ноды в кластер
    """
    __tablename__ = "node_invites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    revoked = Column(Boolean, default=False)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=True)
