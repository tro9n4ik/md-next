"""Проверка месячных периодов, продления и применения ограничений доступа."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.client import Client, ClientProfile
from app.services.client_limits import access_allowed, add_months, monthly_usage, traffic_period
from app.services import traffic_collector
from conftest import TestingSessionLocal


@pytest.mark.parametrize("anchor,now,start,end", [
    ("2024-01-31T15:30:00", "2024-02-29T15:29:00", "2024-01-31T15:30:00", "2024-02-29T15:30:00"),
    ("2024-01-31T15:30:00", "2024-02-29T15:30:00", "2024-02-29T15:30:00", "2024-03-31T15:30:00"),
    ("2024-01-31T15:30:00", "2025-03-15T00:00:00", "2025-02-28T15:30:00", "2025-03-31T15:30:00"),
    ("2025-12-15T10:00:00", "2026-01-15T10:00:00", "2026-01-15T10:00:00", "2026-02-15T10:00:00"),
])
def test_monthly_anchor_does_not_drift(anchor, now, start, end):
    client = Client(created_at=datetime.fromisoformat(anchor))
    result = traffic_period(client, datetime.fromisoformat(now))
    assert result == tuple(datetime.fromisoformat(value).replace(tzinfo=timezone.utc) for value in (start, end))


def test_new_period_restores_quota_without_enabling_manually_disabled_client():
    created = datetime(2026, 1, 31, tzinfo=timezone.utc)
    client = Client(created_at=created, traffic_period_start=created, monthly_traffic_limit=100,
                    monthly_traffic_up=60, monthly_traffic_down=40, is_active=True)
    assert not access_allowed(client, datetime(2026, 2, 27, tzinfo=timezone.utc))
    assert access_allowed(client, datetime(2026, 2, 28, tzinfo=timezone.utc))
    assert monthly_usage(client, datetime(2026, 2, 28, tzinfo=timezone.utc)) == (0, 0)
    client.is_active = False
    assert not access_allowed(client, datetime(2026, 2, 28, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_create_renew_and_custom_validation(auth_headers):
    with patch("app.api.clients._sync_protocols", new=AsyncMock()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
            created = await api.post("/api/v1/clients", headers=auth_headers, json={"name": "Подписка", "subscription_period": "month", "monthly_traffic_limit": 1024})
            assert created.status_code == 201, created.text
            data = created.json()["client"]
            client_id = data["id"]
            expires = datetime.fromisoformat(data["expires_at"])
            assert data["monthly_traffic_limit"] == 1024
            assert data["access_allowed"]
            renewed = await api.put(f"/api/v1/clients/{client_id}", headers=auth_headers, json={"subscription_period": "year"})
            assert renewed.status_code == 200
            assert datetime.fromisoformat(renewed.json()["expires_at"]) == add_months(expires, 12)
            invalid = await api.put(f"/api/v1/clients/{client_id}", headers=auth_headers, json={"subscription_period": "custom", "expires_at": "2020-01-01T00:00:00Z"})
            assert invalid.status_code == 422
            unlimited = await api.put(f"/api/v1/clients/{client_id}", headers=auth_headers, json={"expires_at": None, "monthly_traffic_limit": 0})
            assert unlimited.status_code == 200
            assert unlimited.json()["expires_at"] is None
            assert unlimited.json()["monthly_traffic_limit"] == 0
            negative = await api.post("/api/v1/clients", headers=auth_headers, json={"name": "Ошибка", "monthly_traffic_limit": -1})
            assert negative.status_code == 422


@pytest.mark.asyncio
async def test_expired_subscription_is_empty_with_expire_header_and_status_filter(auth_headers):
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    async with TestingSessionLocal() as db:
        client = Client(name="Истёкший срок", phone="", email="", is_active=True, expires_at=expired, sub_token="expiry-test")
        db.add(client)
        await db.flush()
        db.add(ClientProfile(client_id=client.id, kind="vless_reality_tcp", uuid="expiry-uuid"))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        response = await api.get("/api/v1/sub/expiry-test")
        assert response.status_code == 200 and response.text == ""
        assert f"expire={int(expired.timestamp())}" in response.headers["Subscription-Userinfo"]
        active = await api.get("/api/v1/clients?status=active", headers=auth_headers)
        disabled = await api.get("/api/v1/clients?status=disabled", headers=auth_headers)
        assert active.json() == []
        assert disabled.json()[0]["blocked_reason"] == "expired"


@pytest.mark.asyncio
async def test_collector_enforces_and_retries_then_resumes_at_month_boundary(monkeypatch):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    async with TestingSessionLocal() as db:
        client = Client(name="Лимит", phone="", email="", created_at=start, traffic_period_start=start, monthly_traffic_limit=100)
        db.add(client)
        await db.flush()
        client_id = client.id
        db.add(ClientProfile(client_id=client.id, kind="vless_reality_tcp", uuid="quota-uuid"))
        await db.commit()
    async def command(*args):
        if args[0] == "xray":
            return '{"stat":[{"name":"user>>>c' + str(client_id) + '-vless_reality_tcp@md-next>>>traffic>>>uplink","value":120}]}'
        return ""
    monkeypatch.setattr(traffic_collector, "LIMITS_SYNC_PENDING", False)
    with patch.object(traffic_collector, "_run_command", side_effect=command), \
         patch.object(traffic_collector.ClientService, "sync_xray_clients", return_value=(False, "Проверка не прошла")) as sync, \
         patch.object(traffic_collector.AWGService, "sync_server_config", return_value=(True, "Готово")):
        await traffic_collector._collect_client_traffic()
        assert traffic_collector.LIMITS_SYNC_PENDING
        sync.return_value = (True, "Готово")
        await traffic_collector._collect_client_traffic()
        assert sync.await_count == 2
        assert not traffic_collector.LIMITS_SYNC_PENDING
        async with TestingSessionLocal() as db:
            client = await db.get(Client, client_id)
            assert client.is_active and client.access_blocked
            assert client.monthly_traffic_up == 240
            # Сохраняем исчерпанный предыдущий период, а якорь переносим на месяц назад.
            client.created_at = add_months(start, -1)
            client.traffic_period_start = client.created_at
            await db.commit()
        with patch.object(traffic_collector, "_run_command", return_value="{}"):
            await traffic_collector._collect_client_traffic()
        async with TestingSessionLocal() as db:
            client = await db.get(Client, client_id)
            assert client.is_active and not client.access_blocked
            assert client.monthly_traffic_up == 0
            assert client.traffic_total == 240


@pytest.mark.asyncio
async def test_expiration_and_quota_remove_clients_from_both_server_configs(tmp_path):
    from app.services.client_service import ClientService
    from app.services.awg import AWGService
    now = datetime.now(timezone.utc)
    async with TestingSessionLocal() as db:
        active = Client(name="Разрешён", phone="", email="", created_at=now, is_active=True)
        expired = Client(name="Истёк", phone="", email="", created_at=now, is_active=True, expires_at=now - timedelta(seconds=1))
        quota = Client(name="Исчерпан", phone="", email="", created_at=now, traffic_period_start=now, is_active=True, monthly_traffic_limit=100, monthly_traffic_up=100)
        db.add_all([active, expired, quota])
        await db.flush()
        for index, client in enumerate((active, expired, quota), start=1):
            db.add(ClientProfile(client_id=client.id, kind="vless_reality_tcp", uuid=f"test-uuid-{index}"))
            db.add(ClientProfile(client_id=client.id, kind="awg", public_key=f"test-public-{index}", ip_address=f"10.8.0.{index+1}/32"))
        await db.commit()
        with patch("app.services.client_service.apply_reality_sni", new=AsyncMock()), \
             patch("app.services.xray.XrayService.apply_config", return_value=(True, "Готово")) as apply:
            assert (await ClientService.sync_xray_clients(db))[0]
            assert [item["id"] for item in apply.call_args.kwargs["clients"]] == ["test-uuid-1"]
        path = tmp_path / "awg.conf"
        with patch.object(AWGService, "get_server_settings", return_value={"server_private_key": "тест", "server_ip": "10.8.0.1", "port": 51820}), \
             patch.object(AWGService, "protocol_parameters", return_value={}), \
             patch("app.services.awg.run_cmd", return_value=(0, "[Interface]\nPrivateKey = тест\n", "")):
            assert (await AWGService.sync_server_config(db, str(path)))[0]
        config = path.read_text(encoding="utf-8")
        assert "test-public-1" in config
        assert "test-public-2" not in config and "test-public-3" not in config
