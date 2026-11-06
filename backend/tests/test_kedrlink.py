import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.api import node_control
from app.models.node import Node
from conftest import TestingSessionLocal
from test_subadmins import request, account


async def register_agent(headers, *, host="192.0.2.99", transport="mdnt"):
    invite = await request("POST", "/api/v1/nodes/invites", headers,
                           json={"name": "KedrLink test", "transport": transport})
    assert invite.status_code == 200
    payload = {"token": invite.json()["token"], "host": host, "port": 443, "transport": transport}
    if transport == "mdnt":
        payload["control_protocol"] = 1
    with patch("app.api.nodes.ClientService.sync_xray_clients", new=AsyncMock(return_value=(True, "ok"))):
        response = await request("POST", "/api/v1/nodes/register", {}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def heartbeat(completed=None):
    data = {"version": 1, "snapshot": {"agent_version": "0.1.0", "uptime_ms": 12000}}
    if completed:
        data["completed_command_id"] = completed
    return data


@pytest.mark.asyncio
async def test_status_command_acks_and_private_credentials(auth_headers, monkeypatch):
    data = await register_agent(auth_headers)
    node_id = data["node_id"]
    path = f"/api/v1/nodes/{node_id}/control"
    headers = {"Authorization": "Bearer " + data["control"]["token"]}
    waiting = await request("GET", path, auth_headers)
    assert waiting.json()["state"] == "waiting"
    first = await request("POST", path+"/commands", auth_headers, json={"kind": "status"})
    assert first.status_code == 202
    command_id = first.json()["command"]["id"]
    repeated = await request("POST", path+"/commands", auth_headers, json={"kind": "status"})
    assert repeated.json()["command"]["id"] == command_id
    response = await request("POST", path+"/poll", headers, json=heartbeat())
    assert response.status_code == 200
    assert response.json()["commands"][0]["id"] == command_id
    node_control._last_poll.clear()
    acknowledged = await request("POST", path+"/poll", headers, json=heartbeat(command_id))
    assert acknowledged.json()["commands"] == []
    state = (await request("GET", path, auth_headers)).json()
    assert state["state"] == "online"
    assert state["command"]["status"] == "completed"
    assert state["command"]["result"] == heartbeat()["snapshot"]
    node_control._last_poll.clear()
    await request("POST", path+"/poll", headers, json=heartbeat(command_id))
    assert (await request("GET", path, auth_headers)).json()["command"] == state["command"]
    async with TestingSessionLocal() as db:
        node = await db.get(Node, node_id)
        assert node.protocol == "trojan"  # Management selection does not change VPN protocol.
        assert node.control_token_hash != data["control"]["token"]
        assert len(node.control_token_hash) == 64
    for url in ("/api/v1/nodes", f"/api/v1/nodes/{node_id}", path):
        public = await request("GET", url, auth_headers)
        assert data["control"]["token"] not in public.text
        assert "control_token_hash" not in public.text


@pytest.mark.asyncio
async def test_authentication_scope_and_rate_limits(auth_headers):
    first = await register_agent(auth_headers)
    other = await register_agent(auth_headers, host="192.0.2.100")
    path = f'/api/v1/nodes/{first["node_id"]}/control'
    for headers in ({}, auth_headers, {"Authorization": "Bearer "+other["control"]["token"]}):
        assert (await request("POST", path+"/poll", headers, json=heartbeat())).status_code == 401
    _, limited = await account(auth_headers)
    for body in ({}, heartbeat()):
        assert (await request("POST", path+"/poll", limited, json=body)).status_code == 401
    assert (await request("GET", path, limited)).status_code == 403
    assert (await request("POST", path+"/commands", limited, json={"kind": "status"})).status_code == 403
    headers = {"Authorization": "Bearer "+first["control"]["token"]}
    assert (await request("POST", path+"/poll", headers, json=heartbeat())).status_code == 200
    assert (await request("POST", path+"/poll", headers, json=heartbeat())).status_code == 429
    assert (await request("POST", path+"/commands", auth_headers, json={"kind": "shell"})).status_code == 422
    assert (await request("POST", path+"/commands", auth_headers, json={"kind": "status", "shell": "touch /tmp/forbidden"})).status_code == 422


@pytest.mark.asyncio
async def test_key_rotation_and_switch_back_preserve_standard_nodes(auth_headers):
    first = await register_agent(auth_headers)
    second = await register_agent(auth_headers)
    assert second["node_id"] == first["node_id"]
    assert second["control"]["token"] != first["control"]["token"]
    path = f'/api/v1/nodes/{first["node_id"]}/control'
    old = {"Authorization": "Bearer "+first["control"]["token"]}
    assert (await request("POST", path+"/poll", old, json=heartbeat())).status_code == 401
    standard = await register_agent(auth_headers, transport="xray")
    assert "control" not in standard
    new = {"Authorization": "Bearer "+second["control"]["token"]}
    assert (await request("POST", path+"/poll", new, json=heartbeat())).status_code == 401
    assert (await request("GET", path, auth_headers)).json()["state"] == "standard"
    assert (await request("POST", path+"/commands", auth_headers, json={"kind": "status"})).status_code == 409


@pytest.mark.asyncio
async def test_expired_and_wrong_command_acknowledgements(auth_headers):
    data = await register_agent(auth_headers)
    path = f'/api/v1/nodes/{data["node_id"]}/control'
    await request("POST", path+"/commands", auth_headers, json={"kind": "status"})
    async with TestingSessionLocal() as db:
        node = await db.get(Node, data["node_id"])
        command_id = node.control_command_id
        node.control_command_deadline = dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=1)
        await db.commit()
    headers = {"Authorization": "Bearer "+data["control"]["token"]}
    response = await request("POST", path+"/poll", headers, json=heartbeat(command_id))
    assert response.status_code == 200
    assert response.json()["commands"] == []
    state = (await request("GET", path, auth_headers)).json()
    assert state["command"]["status"] == "expired"
    assert state["command"]["result"] is None
    await request("POST", path+"/commands", auth_headers, json={"kind": "status"})
    node_control._last_poll.clear()
    await request("POST", path+"/poll", headers, json=heartbeat("f"*32))
    assert (await request("GET", path, auth_headers)).json()["command"]["status"] == "pending"
