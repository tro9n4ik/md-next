import secrets
from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    phone = Column(String, nullable=False)
    email = Column(String, nullable=False)
    protocol = Column(String, nullable=True)  # Устаревшее поле для совместимости
    uuid = Column(String, unique=True, index=True, nullable=True)
    public_key = Column(String, nullable=True)
    private_key_hash = Column(String, nullable=True)  # Устаревшее поле для совместимости
    ip_address = Column(String, unique=True, index=True, nullable=True)
    traffic_used = Column(Integer, default=0)  # Суммарное значение для обратной совместимости
    traffic_total = Column(BigInteger, default=0, nullable=False)
    traffic_limit = Column(Integer, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    monthly_traffic_limit = Column(BigInteger, nullable=False, default=0, server_default="0")
    monthly_traffic_up = Column(BigInteger, nullable=False, default=0, server_default="0")
    monthly_traffic_down = Column(BigInteger, nullable=False, default=0, server_default="0")
    cdn_monthly_traffic_limit = Column(BigInteger, nullable=False, default=0, server_default="0")
    cdn_monthly_traffic_up = Column(BigInteger, nullable=False, default=0, server_default="0")
    cdn_monthly_traffic_down = Column(BigInteger, nullable=False, default=0, server_default="0")
    cdn_traffic_up = Column(BigInteger, nullable=False, default=0, server_default="0")
    cdn_traffic_down = Column(BigInteger, nullable=False, default=0, server_default="0")
    cdn_access_blocked = Column(Boolean, nullable=False, default=False, server_default="0")
    traffic_period_start = Column(DateTime(timezone=True), nullable=True)
    access_blocked = Column(Boolean, nullable=False, default=False, server_default="0")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sub_token = Column(String(64), unique=True, index=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    profiles = relationship("ClientProfile", back_populates="client", cascade="all, delete-orphan")


class ClientProfile(Base):
    __tablename__ = "client_profiles"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_client_profiles_uuid"),
        UniqueConstraint("ip_address", name="uq_client_profiles_ip_address"),
    )

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(64), nullable=False, index=True)
    uuid = Column(String(64), nullable=True)
    auth = Column(String(255), nullable=True)
    public_key = Column(String(128), nullable=True)
    private_key_enc = Column(String(512), nullable=True)
    ip_address = Column(String(64), nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    traffic_up = Column(BigInteger, nullable=False, default=0)
    traffic_down = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("Client", back_populates="profiles")
