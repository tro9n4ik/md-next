import os
import datetime
import pytest
import pytest_asyncio
import asyncio
import unittest.mock as mock
from httpx import AsyncClient, ASGITransport
from sqlalchemy.future import select

from app.main import app
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting
from app.models.node_invite import NodeInvite
from app.models.traffic import TrafficSample
from app.bot.bot import bot_manager
from app.services.shell import CommandUnavailable

@pytest.mark.asyncio
async def test_unauthorized_access():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/api/v1/clients")
    assert response.status_code in (401, 403)

@pytest.mark.asyncio
async def test_login_success():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "StrongTestPassword123!"}
        )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data

@pytest.mark.asyncio
async def test_login_failure():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "WrongPassword123"}
        )
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_get_clients(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/api/v1/clients", headers=auth_headers)

    assert response.status_code == 200
    assert isinstance(response.json(), list)

@pytest.mark.asyncio
async def test_client_creation_and_email_validation(auth_headers):
    # Создание клиента без телефона и email проходит успешно
    with mock.patch("app.services.client_service.ClientService.sync_xray_clients", return_value=(True, "OK")), \
         mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "OK")):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            res_no_contacts = await ac.post(
                "/api/v1/clients",
                json={"name": "client_no_contacts", "protocol": "awg"},
                headers=auth_headers
            )
    assert res_no_contacts.status_code == 201
    assert res_no_contacts.json()["client"]["name"] == "client_no_contacts"

    # Неверный формат email возвращает 422
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        res_bad_email = await ac.post(
            "/api/v1/clients",
            json={
                "name": "client_bad_email",
                "email": "invalid-email-address",
                "protocol": "awg"
            },
            headers=auth_headers
        )
    assert res_bad_email.status_code == 422

@pytest.mark.asyncio
async def test_create_vless_client_missing_env(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/clients",
            json={
                "name": "test_vless_client",
                "phone": "+79990000000",
                "email": "test@example.com",
                "protocol": "vless"
            },
            headers=auth_headers
        )
    assert response.status_code == 503

@pytest.mark.asyncio
async def test_create_vless_client_success(auth_headers):
    env_mock = {
        "SERVER_HOST": "1.2.3.4",
        "XRAY_PUBLIC_KEY": "test_pbk",
        "XRAY_SERVER_NAME": "example.com"
    }
    with mock.patch.dict("os.environ", env_mock):
        with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
             mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "ok")):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    "/api/v1/clients",
                    json={
                        "name": "test_vless_client",
                        "phone": "+79990000000",
                        "email": "test@example.com",
                        "protocol": "vless"
                    },
                    headers=auth_headers
                )

            assert response.status_code == 201
            data = response.json()
            assert "client" in data
            assert data["client"]["name"] == "test_vless_client"
            assert data["link"].startswith("vless://")

@pytest.mark.asyncio
async def test_create_awg_client(auth_headers):
    with mock.patch("app.services.client_service.ClientService.sync_xray_clients", return_value=(True, "OK")), \
         mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "OK")):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                "/api/v1/clients",
                json={"name": "test_awg_client", "phone": "+79990000001", "email": "awg@example.com", "protocol": "awg"},
                headers=auth_headers
            )

    assert response.status_code == 201
    data = response.json()
    assert "client" in data
    assert data["client"]["name"] == "test_awg_client"
    assert data["client"]["phone"] == "+79990000001"
    assert data["client"]["email"] == "awg@example.com"
    assert data["client"]["protocol"] == "awg"
    assert "conf" in data
    assert "[Interface]" in data["conf"]


@pytest.mark.asyncio
async def test_missing_system_binary_returns_gateway_error_and_rolls_back(auth_headers):
    async def missing_xray(_db):
        raise CommandUnavailable("Команда Xray не найдена в PATH")

    with mock.patch("app.api.clients._sync_protocols", side_effect=missing_xray):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/clients",
                json={"name": "missing_xray_regression", "protocol": "awg"},
                headers=auth_headers,
            )
    assert response.status_code == 502
    assert "Xray" in response.json()["detail"]

@pytest.mark.asyncio
async def test_change_password_flow(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Неверный старый пароль -> 400
        res_bad_old = await ac.put(
            "/api/v1/auth/password",
            json={"old_password": "WrongOldPassword123!", "new_password": "NewStrongPassword2025!"},
            headers=auth_headers
        )
        assert res_bad_old.status_code == 400

        # 2. Короткий новый пароль (<10 символов) -> 400
        res_short = await ac.put(
            "/api/v1/auth/password",
            json={"old_password": "StrongTestPassword123!", "new_password": "short"},
            headers=auth_headers
        )
        assert res_short.status_code == 400

        # 3. Успешная смена пароля
        res_ok = await ac.put(
            "/api/v1/auth/password",
            json={"old_password": "StrongTestPassword123!", "new_password": "NewStrongPassword2025!"},
            headers=auth_headers
        )
        assert res_ok.status_code == 200

        # 4. Проверка входа с новым паролем
        res_login_new = await ac.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "NewStrongPassword2025!"}
        )
        assert res_login_new.status_code == 200
        new_token = res_login_new.json()["access_token"]
        new_headers = {"Authorization": f"Bearer {new_token}"}

        # 5. Сброс пароля обратно
        await ac.put(
            "/api/v1/auth/password",
            json={"old_password": "NewStrongPassword2025!", "new_password": "StrongTestPassword123!"},
            headers=new_headers
        )

@pytest.mark.asyncio
async def test_node_invites_and_register_flow(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Создаем инвайт: токен показывается ровно 1 раз
        inv_res = await ac.post(
            "/api/v1/nodes/invites",
            json={"name": "test-invite-node", "ttl_hours": 24},
            headers=auth_headers
        )
        assert inv_res.status_code == 200
        inv_data = inv_res.json()
        token = inv_data["token"]
        invite_id = inv_data["id"]

        # В БД лежит только sha256, не открытый токен
        async with AsyncSessionLocal() as session:
            db_inv = (await session.execute(select(NodeInvite).where(NodeInvite.id == invite_id))).scalar_one()
            assert db_inv.token_hash != token
            assert len(db_inv.token_hash) == 64

        # Список инвайтов не содержит открытого токена
        list_res = await ac.get("/api/v1/nodes/invites", headers=auth_headers)
        assert list_res.status_code == 200
        items = list_res.json()
        target_item = next(i for i in items if i["id"] == invite_id)
        assert "token" not in target_item

        # Чужой / неверный токен для join отдаёт 401
        join_bad = await ac.get("/api/v1/nodes/join?token=invalid_token_xyz")
        assert join_bad.status_code == 401

        # Валидный join отдаёт скрипт
        join_res = await ac.get(f"/api/v1/nodes/join?token={token}")
        assert join_res.status_code == 200
        assert "set -e" in join_res.text

        # Успешная регистрация ноды возвращает secret
        with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")):
            reg_res = await ac.post(
                "/api/v1/nodes/register",
                json={
                    "token": token,
                    "host": "192.168.1.100",
                    "port": 443,
                    "protocol": "trojan"
                }
            )
        assert reg_res.status_code == 201
        reg_data = reg_res.json()
        assert reg_data["status"] == "connected"
        assert "secret" in reg_data

        # Повторное использование токена даёт 401
        fail_res = await ac.post(
            "/api/v1/nodes/register",
            json={
                "token": token,
                "host": "192.168.1.101",
                "port": 443,
                "protocol": "trojan"
            }
        )
        assert fail_res.status_code == 401

        # В ответе GET /nodes НЕТ ключа secret
        nodes_res = await ac.get("/api/v1/nodes", headers=auth_headers)
        assert nodes_res.status_code == 200
        nodes_list = nodes_res.json()
        assert len(nodes_list) > 0
        for n in nodes_list:
            assert "secret" not in n

@pytest.mark.asyncio
async def test_expired_and_revoked_node_invites(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Отзыв инвайта
        inv1 = (await ac.post("/api/v1/nodes/invites", json={"name": "to-revoke"}, headers=auth_headers)).json()
        tok1 = inv1["token"]
        id1 = inv1["id"]

        del_res = await ac.delete(f"/api/v1/nodes/invites/{id1}", headers=auth_headers)
        assert del_res.status_code == 204

        # Отозванный токен не дает получить join и не дает зарегистрироваться
        assert (await ac.get(f"/api/v1/nodes/join?token={tok1}")).status_code == 401
        assert (await ac.post("/api/v1/nodes/register", json={"token": tok1, "host": "1.1.1.1", "port": 443})).status_code == 401

        # 2. Истёкший токен
        inv2 = (await ac.post("/api/v1/nodes/invites", json={"name": "expired"}, headers=auth_headers)).json()
        tok2 = inv2["token"]
        id2 = inv2["id"]

        # Руками в БД делаем expires_at в прошлом
        async with AsyncSessionLocal() as session:
            db_inv = (await session.execute(select(NodeInvite).where(NodeInvite.id == id2))).scalar_one()
            db_inv.expires_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=10)
            await session.commit()

        assert (await ac.get(f"/api/v1/nodes/join?token={tok2}")).status_code == 401
        assert (await ac.post("/api/v1/nodes/register", json={"token": tok2, "host": "2.2.2.2", "port": 443})).status_code == 401

@pytest.mark.asyncio
async def test_parallel_register_concurrency(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        inv = (await ac.post("/api/v1/nodes/invites", json={"name": "concurrent-node"}, headers=auth_headers)).json()
        token = inv["token"]

        async def do_reg(ip):
            return await ac.post(
                "/api/v1/nodes/register",
                json={"token": token, "host": ip, "port": 443, "protocol": "trojan"}
            )

        with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")):
            res1, res2 = await asyncio.gather(do_reg("10.0.0.1"), do_reg("10.0.0.2"))
        status_codes = [res1.status_code, res2.status_code]
        assert status_codes.count(201) == 1
        assert status_codes.count(401) == 1

@pytest.mark.asyncio
async def test_node_deletion_flow(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Регистрация ноды
        inv = (await ac.post("/api/v1/nodes/invites", json={"name": "node-to-delete"}, headers=auth_headers)).json()
        with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")):
            reg = (await ac.post("/api/v1/nodes/register", json={"token": inv["token"], "host": "10.0.0.50", "port": 443})).json()
        node_id = reg["node_id"]

        # Если apply_config падает, удаление откатывается и отдаёт 502
        with mock.patch("app.services.xray.XrayService.apply_config", return_value=(False, "Xray validation failed")):
            del_fail = await ac.delete(f"/api/v1/nodes/{node_id}", headers=auth_headers)
            assert del_fail.status_code == 502

        # Удаление 404 для несуществующей ноды
        del_404 = await ac.delete("/api/v1/nodes/999999", headers=auth_headers)
        assert del_404.status_code == 404

        # Успешное удаление
        with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")):
            del_ok = await ac.delete(f"/api/v1/nodes/{node_id}", headers=auth_headers)
            assert del_ok.status_code == 204

@pytest.mark.asyncio
async def test_system_stats_and_traffic(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # Добавим тестовую сэмпл запись трафика
        async with AsyncSessionLocal() as session:
            session.add(TrafficSample(timestamp=datetime.datetime.now(datetime.timezone.utc), rx_bytes=1000, tx_bytes=2000))
            await session.commit()

        # GET /system/stats
        stats_res = await ac.get("/api/v1/system/stats", headers=auth_headers)
        assert stats_res.status_code == 200
        stats_data = stats_res.json()
        assert "cpu_percent" in stats_data
        assert "memory" in stats_data
        assert "net_speed" in stats_data

        # GET /system/traffic
        traffic_res = await ac.get("/api/v1/system/traffic", headers=auth_headers)
        assert traffic_res.status_code == 200
        tr_data = traffic_res.json()
        assert "today" in tr_data
        assert "last_30_days" in tr_data
        assert "all_time" in tr_data
        assert tr_data["today"]["total"] >= 3000

@pytest.mark.asyncio
async def test_telegram_settings_validation_and_encryption(auth_headers):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # PUT с невалидным токеном (getMe замокан на ошибку) -> 400 и НЕ сохраняется в БД
        with mock.patch("app.bot.bot.BotManager.validate_token", side_effect=Exception("Invalid token format")):
            put_bad = await ac.put(
                "/api/v1/settings/telegram",
                json={"token": "12345:BAD_TOKEN_MOCK"},
                headers=auth_headers
            )
            assert put_bad.status_code == 400

        # Токен не попал в БД
        async with AsyncSessionLocal() as session:
            db_s = (await session.execute(select(Setting).where(Setting.key == "telegram_bot_token"))).scalar_one_or_none()
            assert db_s is None or "BAD_TOKEN_MOCK" not in db_s.value

        # PUT с валидным токеном
        with mock.patch("app.bot.bot.BotManager.validate_token", return_value=mock.AsyncMock()):
            with mock.patch("app.bot.bot.BotManager.reload", return_value=None):
                put_ok = await ac.put(
                    "/api/v1/settings/telegram",
                    json={
                        "token": "123456789:ABCDEF_ValidSecretToken",
                        "admin_id": "987654321",
                        "proxy_url": "",
                        "notify_node_down": True,
                        "notify_failover": False
                    },
                    headers=auth_headers
                )
                assert put_ok.status_code == 200

        # В БД токен зашифрован (Fernet)
        async with AsyncSessionLocal() as session:
            db_tok = (await session.execute(select(Setting).where(Setting.key == "telegram_bot_token"))).scalar_one()
            assert "ABCDEF_ValidSecretToken" not in db_tok.value

        # GET /settings/telegram не отдаёт открытый токен
        get_res = await ac.get("/api/v1/settings/telegram", headers=auth_headers)
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["token_set"] is True
        assert "ABCDEF_ValidSecretToken" not in get_data["token_masked"]
        assert get_data["admin_id"] == "987654321"

        # POST /telegram/test при неактивном боте даёт 400
        bot_manager.status = "disabled"
        bot_manager.bot = None
        test_res = await ac.post("/api/v1/settings/telegram/test", headers=auth_headers)
        assert test_res.status_code == 400
