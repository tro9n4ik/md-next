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
                "protocol": "vless", "public_key": KEY, "identity": IDENTITY, "short_id": "0123456789abcdef",
            })
        assert response.status_code == 201
        assert response.json()["secret"] == IDENTITY
        rows = (await client.get("/api/v1/nodes", headers=auth_headers)).json()
        assert all("secret" not in row for row in rows)
    async with AsyncSessionLocal() as db:
        node = (await db.execute(select(Node).where(Node.host == "192.0.2.50"))).scalar_one()
        assert node.public_key == KEY
        assert node.short_id == "0123456789abcdef"
        assert node.protocol == "vless"
        assert node.secret == IDENTITY


def test_node_short_id_is_forwarded_and_validated():
    row = {"id": 1, "host": "192.0.2.1", "port": 443, "protocol": "vless", "secret": IDENTITY, "public_key": KEY}
    assert node_outbound(row)["streamSettings"]["realitySettings"]["shortId"] == ""
    assert node_outbound({**row, "short_id": "0123456789abcdef"})["streamSettings"]["realitySettings"]["shortId"] == "0123456789abcdef"
    for bad in ('1', 'zz', 'a' * 18):
        with pytest.raises(ValueError): node_outbound({**row, "short_id": bad})
        with pytest.raises(ValidationError): NodeRegister(token="test", host="192.0.2.1", port=443, protocol="vless", identity=IDENTITY, public_key=KEY, short_id=bad)

@pytest.mark.asyncio
async def test_warp_probe_uses_reality_for_secure_node(monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from app.services import warp_node
    monkeypatch.setattr(warp_node, "find_command", lambda _: "/usr/local/bin/xray")
    async def capture(*args, **kwargs):
        config = json.loads(Path(args[-1]).read_text())
        tunnel = next(row for row in config["outbounds"] if row["tag"] == "node-1")
        assert tunnel["protocol"] == "vless"
        assert tunnel["streamSettings"]["security"] == "reality"
        assert config["outbounds"][0]["streamSettings"]["sockopt"]["dialerProxy"] == "node-1"
        raise RuntimeError("synthetic stop before process creation")
    monkeypatch.setattr(warp_node.asyncio, "create_subprocess_exec", capture)
    node = SimpleNamespace(id=1, host="192.0.2.1", port=443, protocol="vless", secret=IDENTITY, public_key=KEY, is_enabled=True)
    with pytest.raises(RuntimeError, match="synthetic stop"):
        await warp_node.test_node_proxy(node, 40000, None)
