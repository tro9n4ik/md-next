from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import clients as clients_api
from app.db.database import AsyncSessionLocal
from app.main import app
from app.models.client import Client, ClientGroup, ClientProfile
from app.services import client_activity


@pytest.mark.asyncio
@pytest.mark.parametrize("order, expected", [
    ("desc", ["Онлайн А", "Онлайн Я", "Офлайн", "Приостановлен", "Нет данных"]),
    ("asc", ["Офлайн", "Приостановлен", "Онлайн А", "Онлайн Я", "Нет данных"]),
])
async def test_online_sort_uses_actual_activity_and_keeps_filters(auth_headers, monkeypatch, order, expected):
    monkeypatch.setattr(client_activity, "_seen", {})
    monkeypatch.setattr(client_activity, "_sources", {})
    monkeypatch.setattr(clients_api, "enabled_profile_kinds", AsyncMock(return_value={"vless_reality_tcp", "awg"}))
    async with AsyncSessionLocal() as db:
        group = ClientGroup(name="Проверка", name_key="проверка", color="slate")
        db.add(group)
        await db.flush()
        group_id = group.id
        for name, active, kind, traffic in (
            ("Онлайн Я", True, "vless_reality_tcp", 100),
            ("Нет данных", True, "awg", 0),
            ("Офлайн", True, "vless_reality_tcp", 0),
            ("Онлайн А", True, "vless_reality_tcp", 100),
            ("Приостановлен", False, "vless_reality_tcp", 100),
        ):
            row = Client(name=name, phone="", email="", is_active=active, group_id=group_id)
            db.add(row)
            await db.flush()
            db.add(ClientProfile(client_id=row.id, kind=kind, is_enabled=True))
            client_activity.record_activity(row.id, kind, traffic, 0)
        db.add(Client(name="Другая группа", phone="", email=""))
        await db.commit()
    client_activity.source_checked("xray")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/v1/clients?sort=online&order={order}&group_id={group_id}"
        response = await client.get(url, headers=auth_headers)
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row["name"] for row in rows] == expected
        assert all(row["connection_status"] == "online" for row in rows if row["name"].startswith("Онлайн"))
        assert rows[-1]["connection_status"] == "unknown"
        filtered = await client.get(url + "&q=Онлайн&status=active", headers=auth_headers)
        assert filtered.status_code == 200, filtered.text
        assert [row["name"] for row in filtered.json()] == ["Онлайн А", "Онлайн Я"]
