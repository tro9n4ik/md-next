from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.db.database import Base

class RoutingRule(Base):
    """
    Модель правила маршрутизации трафика
    """
    __tablename__ = "routing_rules"

    id = Column(Integer, primary_key=True, index=True)
    domain_or_ip = Column(String, nullable=False, index=True) # Например: "geosite:google", "domain:instagram.com", "1.1.1.1/32"
    target_node_id = Column(Integer, nullable=True) # ID ноды (NULL = напрямую / freedom)
    action = Column(String, default="proxy") # "proxy" или "direct" или "block"
    description = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
