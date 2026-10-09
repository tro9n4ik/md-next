from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.nodes import NodeRegister
from app.services.node_transport import node_outbound

IDENTITY = "e069b4f9-c397-4df0-94af-d78d77b4ac94"
KEY = "A" * 43


def test_reality_transport_requires_verified_peer_key():
    row = {"id": 1, "host": "192.0.2.1", "port": 443, "protocol": "vless", "secret": IDENTITY, "public_key": KEY}
    result = node_outbound(row)
    assert result["streamSettings"]["security"] == "reality"
    assert result["streamSettings"]["realitySettings"]["password"] == KEY
    assert result["streamSettings"]["realitySettings"]["fingerprint"] == "firefox"
    assert result["settings"]["vnext"][0]["users"][0]["id"] == IDENTITY
    with pytest.raises(ValueError):
        node_outbound({**row, "public_key": None})


def test_legacy_node_is_not_silently_reconfigured():
    result = node_outbound({"id": 2, "host": "192.0.2.2", "port": 443, "protocol": "trojan", "secret": "synthetic"})
    assert result["streamSettings"]["network"] == "grpc"
    assert result["settings"]["servers"][0]["password"] == "synthetic"


def test_registration_rejects_incomplete_reality():
    data = {"token": "synthetic", "host": "192.0.2.1", "port": 443, "protocol": "vless"}
    with pytest.raises(ValidationError):
        NodeRegister(**data)
    parsed = NodeRegister(**data, public_key=KEY, identity=IDENTITY)
    assert parsed.identity == UUID(IDENTITY)
    with pytest.raises(ValidationError):
        NodeRegister(**data, public_key="bad", identity=IDENTITY)

@pytest.mark.asyncio
async def test_secure_registration_persists_only_public_key(auth_headers):
    from unittest.mock import patch
    from httpx import AsyncClient, ASGITransport
    from sqlalchemy import select
    from app.main import app
    from app.db.database import AsyncSessionLocal
    from app.models.node import Node
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invite = (await client.post("/api/v1/nodes/invites", headers=auth_headers, json={"name": "secure-demo"})).json()
        with patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")):
            response = await client.post("/api/v1/nodes/register", json={
                "token": invite["token"], "host": "192.0.2.50", "port": 443,
                "protocol": "vless", "public_key": KEY, "identity": IDENTITY,
            })
        assert response.status_code == 201
        assert response.json()["secret"] == IDENTITY
        rows = (await client.get("/api/v1/nodes", headers=auth_headers)).json()
        assert all("secret" not in row for row in rows)
    async with AsyncSessionLocal() as db:
        node = (await db.execute(select(Node).where(Node.host == "192.0.2.50"))).scalar_one()
        assert node.public_key == KEY
        assert node.protocol == "vless"
        assert node.secret == IDENTITY
