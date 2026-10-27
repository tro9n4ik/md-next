from sqlalchemy import Column, Integer, String, Boolean
from app.db.database import Base

class User(Base):
    """
    Модель пользователя панели администратора
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    totp_secret = Column(String, nullable=True) # Действующий секретный ключ для TOTP (Google Authenticator)
    totp_pending_secret = Column(String, nullable=True) # Ожидающий подтверждения секретный ключ для 2FA
    totp_enabled = Column(Boolean, default=False)
    token_version = Column(Integer, default=1, nullable=False)
    role = Column(String(16), nullable=False, default="admin", server_default="admin")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    view_all_clients = Column(Boolean, nullable=False, default=False, server_default="0")
    can_issue_subscriptions = Column(Boolean, nullable=False, default=True, server_default="1")
    can_delete_subscriptions = Column(Boolean, nullable=False, default=False, server_default="0")
    can_pause_subscriptions = Column(Boolean, nullable=False, default=False, server_default="0")
