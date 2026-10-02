from sqlalchemy import Column, DateTime, Index, Integer, JSON, String
from sqlalchemy.sql import func

from app.db.database import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_ts", "ts"),
        Index("ix_events_level_ts", "level", "ts"),
        Index("ix_events_category_ts", "category", "ts"),
    )

    id = Column(Integer, primary_key=True)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    level = Column(String(16), nullable=False, default="info")
    category = Column(String(64), nullable=False)
    message = Column(String(512), nullable=False)
    meta = Column(JSON, nullable=True)
