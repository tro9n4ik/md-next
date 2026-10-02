from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.db.database import Base

class Node(Base):
    """
    Модель узла в кластере MD-Next
    """
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    host = Column(String, nullable=False)
    port = Column(Integer, nullable=False)
    protocol = Column(String, nullable=False) # 'vless' или 'trojan'
    public_key = Column(String, nullable=True) # Публичный ключ для Reality
    secret = Column(String, nullable=True) # Пароль ноды
    is_active = Column(Boolean, default=True)
    is_enabled = Column(Boolean, default=True)
    status = Column(String, default="healthy") # "healthy", "unhealthy", "disabled"
    ping_ms = Column(Integer, default=0)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    priority = Column(Integer, nullable=False, default=0, server_default="0", index=True)
