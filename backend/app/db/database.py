from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
import os
from sqlalchemy import text

# Асинхронный URL для SQLite
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./md_next.db"

# Создание асинхронного движка
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False}
)

# Фабрика асинхронных сессий
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Базовый класс для декларативных моделей
Base = declarative_base()

async def get_db():
    """
    Генератор сессий базы данных для инъекции зависимостей FastAPI
    """
    async with AsyncSessionLocal() as session:
        if os.getenv('MDNEXT_MIGRATION_STAGING') == '1':
            await session.execute(text('PRAGMA query_only=ON'))
        yield session
