import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_password_change_invalidates_old_jwt():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Логинимся
        login_res = await ac.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "StrongTestPassword123!"
        })
        assert login_res.status_code == 200
        token_1 = login_res.json()["access_token"]
        headers_1 = {"Authorization": f"Bearer {token_1}"}

        # 2. Проверяем, что токен 1 работает
        me_res1 = await ac.get("/api/v1/auth/me", headers=headers_1)
        assert me_res1.status_code == 200

        # 3. Меняем пароль через токен 1
        change_res = await ac.put("/api/v1/auth/password", json={
            "old_password": "StrongTestPassword123!",
            "new_password": "NewStrongPassword456!"
        }, headers=headers_1)
        assert change_res.status_code == 200

        # 4. Проверяем, что токен 1 БОЛЬШЕ НЕ РАБОТАЕТ (401)
        me_res2 = await ac.get("/api/v1/auth/me", headers=headers_1)
        assert me_res2.status_code == 401

        # 5. Вход по старому паролю -> 401
        old_login = await ac.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "StrongTestPassword123!"
        })
        assert old_login.status_code == 401

        # 6. Вход по новому паролю -> Успешно, получаем токен 2
        new_login = await ac.post("/api/v1/auth/login", json={
            "username": "admin",
            "password": "NewStrongPassword456!"
        })
        assert new_login.status_code == 200
        token_2 = new_login.json()["access_token"]
        headers_2 = {"Authorization": f"Bearer {token_2}"}

        # 7. Проверяем, что токен 2 работает
        me_res3 = await ac.get("/api/v1/auth/me", headers=headers_2)
        assert me_res3.status_code == 200
