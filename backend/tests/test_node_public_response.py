import pytest

from app.models.node import Node
from conftest import TestingSessionLocal
from test_subadmins import account, request


def test_node_event_projects_model_and_filters_raw_metadata(monkeypatch):
    from app.services import events
    node = Node(id=1, name="NL", host="192.0.2.99", port=443, secret="synthetic-secret",
                country_code="NL", status="healthy", is_active=True, is_enabled=True)
    recorded = []
    monkeypatch.setattr(events, "log_event", lambda *args: recorded.append(args))
    events.log_node_event("info", "Обновление", node)
    payload = recorded[0][3]
    assert payload["country"] == "NL" and payload["is_online"]
    raw = {**node.__dict__, "internal_ip": "192.0.2.33", "shared_secret": "synthetic-secret"}
    for value in (payload, events.event_meta("node", raw)):
        assert not {"host", "port", "secret", "shared_secret", "internal_ip", "_sa_instance_state"} & value.keys()
        assert "192.0.2" not in str(value) and "synthetic-secret" not in str(value)


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
