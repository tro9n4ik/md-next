from sqlalchemy import Column, Integer, BigInteger, DateTime
from sqlalchemy.sql import func
from app.db.database import Base

class TrafficSample(Base):
    """
    Модель замера сетевого трафика
    """
    __tablename__ = "traffic_samples"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    rx_bytes = Column(BigInteger, default=0)
    tx_bytes = Column(BigInteger, default=0)
