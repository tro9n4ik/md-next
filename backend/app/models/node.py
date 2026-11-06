from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, Text
from sqlalchemy.sql import func
from sqlalchemy.types import TypeDecorator
from app.db.database import Base
from app.services.crypto import encrypt_node_secret, decrypt_node_secret


class NodeSecret(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_node_secret(value)

    def process_result_value(self, value, dialect):
        return decrypt_node_secret(value)


class Node(Base):
    """
    Модель узла в кластере MD-Next
    """
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    host = Column(String, nullable=False)
    country_code = Column(String(2), nullable=True)
    port = Column(Integer, nullable=False)
    protocol = Column(String, nullable=False) # 'vless' или 'trojan'
    transport = Column(String(16), nullable=False, default="xray", server_default="xray")
    control_token_hash = Column(String(64), nullable=True)
    control_seen_at = Column(DateTime(timezone=True), nullable=True)
    control_agent_version = Column(String(32), nullable=True)
    control_uptime_ms = Column(BigInteger, nullable=True)
    control_command_id = Column(String(32), nullable=True)
    control_command_deadline = Column(DateTime(timezone=True), nullable=True)
    control_command_result = Column(Text, nullable=True)
    control_command_finished_at = Column(DateTime(timezone=True), nullable=True)
    public_key = Column(String, nullable=True) # Публичный ключ для Reality
    short_id = Column(String(16), nullable=True) # Legacy nodes retain empty shortId
    secret = Column(NodeSecret(), nullable=True)
    is_active = Column(Boolean, default=True)
    is_enabled = Column(Boolean, default=True)
    status = Column(String, default="healthy") # "healthy", "unhealthy", "disabled"
    ping_ms = Column(Integer, default=0)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    priority = Column(Integer, nullable=False, default=0, server_default="0", index=True)
