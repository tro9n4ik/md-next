from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Index
from app.db.database import Base


class NodeSample(Base):
    __tablename__ = 'node_samples'
    __table_args__ = (Index('ix_node_samples_node_ts', 'node_id', 'ts'),)
    id = Column(Integer, primary_key=True)
    node_id = Column(Integer, nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False, index=True)
    healthy = Column(Boolean, nullable=False)
    ping_ms = Column(Integer, nullable=False)
    reason = Column(String(64), nullable=False)


class TelegramLink(Base):
    __tablename__ = 'telegram_links'
    client_id = Column(Integer, ForeignKey('clients.id', ondelete='CASCADE'), primary_key=True)
    telegram_id = Column(String(32), nullable=True, unique=True)
    code_hash = Column(String(64), nullable=True, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

