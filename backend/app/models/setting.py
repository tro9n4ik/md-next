from sqlalchemy import Column, String
from app.db.database import Base

class Setting(Base):
    """
    Модель для хранения глобальных настроек
    """
    __tablename__ = "settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(String, nullable=False)
