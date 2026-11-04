import pytest

from app.models.node import Node
from conftest import TestingSessionLocal
from test_subadmins import account, request


@pytest.mark.asyncio
async def test_public_nodes_have_only_allowlisted_fields(auth_headers):
    _, headers = await account(auth_headers)
    async with TestingSessionLocal() as db:
        db.add(Node(id=10, name="Нидерланды", host="192.0.2.15", port=4443,
                    protocol="trojan", secret="synthetic-private-secret", country_code="NL"))
        await db.commit()
    for url in ("/api/v1/nodes", "/api/v1/nodes/10"):
        response = await request("GET", url, headers)
        assert response.status_code == 200
        data = response.json()
        row = data[0] if isinstance(data, list) else data
        assert set(row) == {"id", "name", "country", "status", "is_online", "last_seen"}
        assert row["country"] == "NL"
        assert "192.0.2.15" not in response.text
        admin = await request("GET", url, auth_headers)
        assert admin.status_code == 200
        assert "192.0.2.15" in admin.text
        assert "synthetic-private-secret" not in admin.text
    assert (await request("GET", "/api/v1/cluster/route", headers)).status_code == 403
