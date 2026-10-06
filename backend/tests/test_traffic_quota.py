import pytest
import unittest.mock as mock
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.db.database import AsyncSessionLocal
from app.models.client import Client

@pytest.mark.asyncio
async def test_client_traffic_quota_enforcement(auth_headers):
    async with AsyncSessionLocal() as session:
        client_obj = Client(
            name="Quota Client",
            phone="",
            email="",
            protocol="vless",
            uuid="33333333-3333-3333-3333-333333333333",
            traffic_used=900,
            traffic_limit=1000, # Лимит 1000 байт
            is_active=True
        )
        session.add(client_obj)
        await session.commit()
        c_id = client_obj.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Добавляем 50 байт -> всего 950 байт (< 1000) -> клиент остаётся активным
        with mock.patch("app.services.client_service.ClientService.sync_xray_clients", return_value=(True, "OK")), \
             mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "OK")):
            res1 = await ac.post(f"/api/v1/clients/{c_id}/traffic", json={"bytes_used": 50}, headers=auth_headers)
            assert res1.status_code == 200
            data1 = res1.json()
            assert data1["traffic_used"] == 950
            assert data1["is_active"] is True

            # Добавляем еще 100 байт -> всего 1050 байт (> 1000) -> клиент БЛОКИРУЕТСЯ
            res2 = await ac.post(f"/api/v1/clients/{c_id}/traffic", json={"bytes_used": 100}, headers=auth_headers)
            assert res2.status_code == 200
            data2 = res2.json()
            assert data2["traffic_used"] == 1050
            assert data2["is_active"] is False

            # Сбрасываем трафик
            res3 = await ac.post(f"/api/v1/clients/{c_id}/reset-traffic", headers=auth_headers)
            assert res3.status_code == 200
            assert res3.json()["traffic_used"] == 0

@pytest.mark.asyncio
async def test_health_endpoint_real_services():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code in (200, 503)
        data = res.json()
        assert set(data) == {"status"}
