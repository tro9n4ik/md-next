"""Transport negotiation is pinned to the invitation, never client-controlled."""
import pytest
from sqlalchemy import select

from app.api.nodes import _hash_token
from app.models.node_invite import NodeInvite
from conftest import TestingSessionLocal
from test_subadmins import request, account


@pytest.mark.asyncio
async def test_existing_invites_keep_standard_transport(auth_headers):
    result = await request("POST", "/api/v1/nodes/invites", auth_headers, json={"name": "standard"})
    assert result.status_code == 200
    assert result.json()["transport"] == "xray"
    script = await request("GET", "/api/v1/nodes/join", {}, params={"token": result.json()["token"]})
    assert script.status_code == 200
    assert "TRANSPORT_INJECTED=xray" in script.text


@pytest.mark.asyncio
async def test_transport_mismatch_does_not_consume_invitation(auth_headers):
    result = await request("POST", "/api/v1/nodes/invites", auth_headers,
                           json={"name": "experimental", "transport": "mdnt"})
    assert result.status_code == 200
    token = result.json()["token"]
    for transport in ("xray",):
        registration = await request("POST", "/api/v1/nodes/register", {},
                                     json={"token": token, "host": "192.0.2.12", "port": 443, "transport": transport})
        assert registration.status_code == 409
        async with TestingSessionLocal() as db:
            invite = (await db.execute(select(NodeInvite).where(NodeInvite.token_hash == _hash_token(token)))).scalar_one()
            assert invite.used_at is None
            assert invite.node_id is None
            assert invite.transport == "mdnt"
    # The selected management channel is injected into the installer.
    script = await request("GET", "/api/v1/nodes/join", {}, params={"token": token})
    assert script.status_code == 200
    assert "TRANSPORT_INJECTED=mdnt" in script.text
    assert "__KEDRLINK_SOURCE__" not in script.text


@pytest.mark.asyncio
async def test_transport_is_validated_and_admin_only(auth_headers):
    unknown = await request("POST", "/api/v1/nodes/invites", auth_headers, json={"transport": "unknown"})
    assert unknown.status_code == 422
    _, limited = await account(auth_headers)
    forbidden = await request("POST", "/api/v1/nodes/invites", limited, json={"transport": "mdnt"})
    assert forbidden.status_code == 403

