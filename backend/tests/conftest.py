import os
import tempfile

# Устанавливаем переменные окружения ДО любых импортов приложения
os.environ["JWT_SECRET_KEY"] = "test_jwt_secret_key_for_pytest_12345"
os.environ["INITIAL_ADMIN_PASSWORD"] = "StrongTestPassword123!"
os.environ["XRAY_PRIVATE_KEY"] = "test_xray_private_key"
os.environ["XRAY_PUBLIC_KEY"] = "test_xray_public_key"
os.environ["XRAY_SERVER_NAME"] = "example.com"
os.environ["XRAY_CONFIG_PATH"] = os.path.join(tempfile.gettempdir(), "test_xray_config.json")
os.environ["TESTING"] = "true"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.database as db_module
from app.db.database import Base, get_db
from app.models.user import User
from app.api.auth import create_access_token, get_password_hash

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

# Переопределяем AsyncSessionLocal и engine глобально в модуле базы данных для тестов
db_module.engine = test_engine
db_module.AsyncSessionLocal = TestingSessionLocal

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        admin_user = User(
            username="admin",
            hashed_password=get_password_hash("StrongTestPassword123!"),
            totp_enabled=False,
            token_version=1
        )
        session.add(admin_user)
        await session.commit()

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

from app.main import app
app.dependency_overrides[get_db] = override_get_db

@pytest.fixture
def auth_headers():
    token = create_access_token({"sub": "admin"})
    return {"Authorization": f"Bearer {token}"}
