import base64
import json
import shutil
import subprocess
import unittest.mock as mock
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.clients import SUBSCRIPTION_REQUESTS
from app.db.database import AsyncSessionLocal
from app.main import app
from app.models.client import Client, ClientProfile
from app.services import traffic_collector
from app.services.xray import XrayService


@pytest.mark.skipif(shutil.which("xray") is None, reason="Xray binary is not installed")
def test_all_profile_inbounds_pass_xray_config_validation(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, x25519
    from cryptography.x509.oid import NameOID

    cert_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
    now = datetime.now(timezone.utc)
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(cert_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1)).sign(cert_key, hashes.SHA256())
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(cert_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))

    reality_key = x25519.X25519PrivateKey.generate()
    reality_private = base64.urlsafe_b64encode(reality_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode().rstrip("=")
    options = {
        "enabled": {"vless_reality_tcp", "vless_xhttp_reality", "vless_xhttp_tls", "hysteria2", "awg"},
        "vless_xhttp_reality_clients": [{"id": str(uuid.uuid4()), "email": "c1-vless_xhttp_reality@md-next"}],
        "vless_xhttp_tls_clients": [{"id": str(uuid.uuid4()), "email": "c1-vless_xhttp_tls@md-next"}],
        "hysteria2_clients": [{"auth": "test-auth", "email": "c1-hysteria2@md-next"}],
        "xhttp_reality_path": "/reality", "xhttp_tls_path": "/secret-path",
        "xhttp_reality_port": 2053, "hysteria2_port": 443,
        "tls_cert": str(cert_path), "tls_key": str(key_path),
    }
    config = XrayService.generate_config(
        clients=[{"id": str(uuid.uuid4()), "flow": "xtls-rprx-vision", "email": "c1-vless_reality_tcp@md-next"}],
        server_private_key=reality_private, server_name="example.com", profile_options=options,
    )
    config_path = tmp_path / "xray.json"
    config_path.write_text(config, encoding="utf-8")
    result = subprocess.run([shutil.which("xray"), "run", "-test", "-format", "json", "-config", str(config_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.asyncio
async def test_create_client_creates_each_enabled_profile_and_awg_config(auth_headers):
    with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
         mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "ok")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/clients", json={"name": "Profile client"}, headers=auth_headers)
            assert response.status_code == 201
            created = response.json()
            assert "[Interface]" in created["conf"]
            profile_res = await client.get(f"/api/v1/clients/{created['client']['id']}/profiles", headers=auth_headers)
            assert profile_res.status_code == 200
            kinds = {p["kind"] for p in profile_res.json()["profiles"]}
            assert kinds == {"vless_reality_tcp", "awg"}
            awg_profile = next(p for p in profile_res.json()["profiles"] if p["kind"] == "awg")
            assert awg_profile["data"].startswith("[Interface]")


@pytest.mark.asyncio
async def test_create_client_rolls_back_when_xray_apply_raises(auth_headers):
    with mock.patch("app.services.xray.XrayService.apply_config", side_effect=RuntimeError("validation failed")), \
         mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "ok")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/clients", json={"name": "Must roll back"}, headers=auth_headers)
        assert response.status_code == 502
    async with AsyncSessionLocal() as session:
        found = await session.execute(select(Client).where(Client.name == "Must roll back"))
        assert found.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_subscription_headers_base64_disable_and_unknown_token():
    async with AsyncSessionLocal() as session:
        client_row = Client(name="Юзер", phone="", email="", is_active=True, sub_token="subscription-test-token")
        session.add(client_row)
        await session.flush()
        session.add(ClientProfile(client_id=client_row.id, kind="vless_reality_tcp", uuid="00000000-0000-0000-0000-000000000123", is_enabled=True, traffic_up=12, traffic_down=34))
        await session.commit()
        client_id = client_row.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/sub/subscription-test-token")
        assert response.status_code == 200
        assert base64.b64decode(response.text).decode().startswith("vless://")
        assert response.headers["profile-title"] == "base64:" + base64.b64encode("MD-NEXT".encode()).decode()
        assert response.headers["Subscription-Userinfo"] == "upload=12; download=34; total=0"
        assert response.headers["profile-update-interval"] == "12"
        invalid = await client.get("/api/v1/sub/unknown-token")
        assert invalid.status_code == 404
        async with AsyncSessionLocal() as session:
            row = await session.get(Client, client_id)
            row.is_active = False
            await session.commit()
        disabled = await client.get("/api/v1/sub/subscription-test-token")
        assert disabled.status_code == 200
        assert disabled.text == ""


@pytest.mark.asyncio
async def test_subscription_regeneration_invalidates_old_url(auth_headers):
    async with AsyncSessionLocal() as session:
        client_row = Client(name="Rotate", phone="", email="", sub_token="old-subscription-token")
        session.add(client_row)
        await session.commit()
        client_id = client_row.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rotated = await client.post(f"/api/v1/clients/{client_id}/sub/regenerate", headers=auth_headers)
        assert rotated.status_code == 200
        new_url = rotated.json()["subscription_url"]
        new_token = new_url.rsplit("/", 1)[-1]
        assert new_token != "old-subscription-token"
        assert (await client.get("/api/v1/sub/old-subscription-token")).status_code == 404
        assert (await client.get(f"/api/v1/sub/{new_token}")).status_code == 200


@pytest.mark.asyncio
async def test_profile_can_be_toggled_and_regenerated(auth_headers):
    async with AsyncSessionLocal() as session:
        client_row = Client(name="Rotate profile", phone="", email="")
        session.add(client_row)
        await session.flush()
        profile = ClientProfile(client_id=client_row.id, kind="vless_reality_tcp", uuid="00000000-0000-0000-0000-000000000456", is_enabled=True)
        session.add(profile)
        await session.commit()
        client_id, profile_id, original_uuid = client_row.id, profile.id, profile.uuid
    with mock.patch("app.services.xray.XrayService.apply_config", return_value=(True, "ok")), \
         mock.patch("app.api.clients.AWGService.sync_server_config", return_value=(True, "ok")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            disabled = await client.put(f"/api/v1/clients/{client_id}/profiles/{profile_id}", json={"is_enabled": False}, headers=auth_headers)
            assert disabled.status_code == 200 and disabled.json()["is_enabled"] is False
            regenerated = await client.post(f"/api/v1/clients/{client_id}/profiles/{profile_id}/regenerate", headers=auth_headers)
            assert regenerated.status_code == 200
    async with AsyncSessionLocal() as session:
        current = await session.get(ClientProfile, profile_id)
        assert current.uuid != original_uuid


@pytest.mark.asyncio
async def test_client_sort_search_and_status_filters(auth_headers):
    async with AsyncSessionLocal() as session:
        session.add_all([
            Client(name="Zulu", phone="", email="find@example.com", is_active=True),
            Client(name="Alpha", phone="", email="", is_active=False),
        ])
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/api/v1/clients?sort=name&order=asc", headers=auth_headers)
        assert [item["name"] for item in result.json()] == ["Alpha", "Zulu"]
        searched = await client.get("/api/v1/clients?q=find&status=active", headers=auth_headers)
        assert [item["name"] for item in searched.json()] == ["Zulu"]


@pytest.mark.asyncio
async def test_awg_traffic_counter_reset_and_quota_disable():
    async with AsyncSessionLocal() as session:
        client_row = Client(name="Limited", phone="", email="", traffic_limit=100, is_active=True)
        session.add(client_row)
        await session.flush()
        session.add(ClientProfile(client_id=client_row.id, kind="awg", public_key="test-public-key", ip_address="10.0.0.2/32", is_enabled=True))
        await session.commit()
        client_id = client_row.id

    traffic_collector.AWG_LAST_COUNTERS = {"test-public-key": (80, 70)}
    async def command(*args):
        if args[0] == "xray":
            return json.dumps({"stat": []})
        return "test-public-key\t140\t130\n"

    with mock.patch("app.services.traffic_collector._run_command", side_effect=command), \
         mock.patch("app.services.client_service.ClientService.sync_xray_clients", return_value=(True, "ok")), \
         mock.patch("app.services.awg.AWGService.sync_server_config", return_value=(True, "ok")):
        await traffic_collector._collect_client_traffic()
    async with AsyncSessionLocal() as session:
        current_client = await session.get(Client, client_id)
        profile = (await session.execute(select(ClientProfile).where(ClientProfile.client_id == client_id))).scalar_one()
        assert profile.traffic_up == 60
        assert profile.traffic_down == 60
        assert current_client.traffic_total == 120
        assert current_client.is_active is False
