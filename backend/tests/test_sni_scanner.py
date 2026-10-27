import asyncio
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting
from app.models.user import User
from app.services import sni_scanner as scanner


@pytest.mark.parametrize(
    "domain",
    [
        "https://example.com",
        "example.com:443",
        "example.com/a",
        "127.0.0.1",
        "[::1]",
        "a..com",
        "-x.com",
        "x_.com",
        "foo.local",
        "foo.internal",
        "foo.test",
        "foo.com\r\nHost: x",
    ],
)
def test_rejects_non_domain_input(domain):
    with pytest.raises(ValueError):
        scanner.normalize_domains([domain])


def test_normalizes_idna_and_deduplicates():
    assert scanner.normalize_domains(["EXAMPLE.COM.", "example.com", "пример.рф"]) == [
        "example.com",
        "xn--e1afmkfd.xn--p1ai",
    ]
    with pytest.raises(ValueError):
        scanner.normalize_domains(["example.com"] * 13)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "ff02::1",
    ],
)
async def test_mixed_dns_cannot_reach_internal_network(address):
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
        for ip in ["1.1.1.1", address]
    ]
    with patch.object(
        asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=records)
    ):
        with pytest.raises(ValueError):
            await scanner.resolve_public("example.com")


def connection(alpn="h2", line=b"HTTP/1.1 200 OK\r\n"):
    reader = MagicMock()
    reader.readline = AsyncMock(return_value=line)
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    tls = MagicMock()
    tls.version.return_value = "TLSv1.3"
    tls.selected_alpn_protocol.return_value = alpn
    writer.get_extra_info.return_value = tls
    return reader, writer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,eligible",
    [
        (200, True),
        (204, True),
        (301, False),
        (302, False),
        (403, False),
        (429, False),
        (500, False),
    ],
)
async def test_certificate_h2_and_http_result(status, eligible):
    first = connection()
    second = connection("http/1.1", f"HTTP/1.1 {status} Test\r\n".encode())
    with patch.object(
        scanner, "resolve_public", AsyncMock(return_value=["1.1.1.1"])
    ), patch.object(
        scanner, "tls_connect", AsyncMock(side_effect=[first, second])
    ) as connect:
        result = await scanner.probe_domain("example.com")
    assert result["eligible"] is eligible
    assert result["http_status"] == status
    assert result["certificate_ok"] and result["tls_version"] == "TLSv1.3"
    assert connect.call_args_list[1].args[:2] == ("1.1.1.1", "example.com")
    for _, writer in [first, second]:
        writer.close.assert_called_once()
    assert connect.await_count == 2  # Redirects are never followed.


@pytest.mark.asyncio
async def test_missing_h2_never_marks_candidate_compatible():
    first = connection(alpn=None)
    with patch.object(
        scanner, "resolve_public", AsyncMock(return_value=["1.1.1.1"])
    ), patch.object(scanner, "tls_connect", AsyncMock(return_value=first)) as connect:
        result = await scanner.probe_domain("example.com")
    assert not result["eligible"]
    assert connect.await_count == 1
    first[1].close.assert_called_once()


@pytest.mark.asyncio
async def test_timeout_closes_http_connection():
    first, second = connection(), connection()

    async def delayed():
        await asyncio.sleep(1)

    second[0].readline.side_effect = delayed
    with patch.object(
        scanner, "resolve_public", AsyncMock(return_value=["1.1.1.1"])
    ), patch.object(
        scanner, "tls_connect", AsyncMock(side_effect=[first, second])
    ), patch.object(
        scanner, "SCAN_TIMEOUT", 0.02
    ):
        result = await scanner.probe_domain("example.com")
    assert not result["eligible"]
    assert result["reason"] == "Время проверки истекло"
    second[1].close.assert_called_once()


@pytest.mark.asyncio
async def test_tls_context_verifies_certificate_and_pins_ip():
    with patch.object(
        scanner.asyncio, "open_connection", AsyncMock(return_value=connection())
    ) as connect:
        await scanner.tls_connect("1.1.1.1", "example.com", ["h2"])
    kwargs = connect.call_args.kwargs
    assert kwargs["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["ssl"].check_hostname
    assert kwargs["ssl"].minimum_version == ssl.TLSVersion.TLSv1_3
    assert kwargs["server_hostname"] == "example.com"
    assert connect.call_args.args == ("1.1.1.1", 443)


@pytest.mark.asyncio
async def test_certificate_error_is_not_eligible():
    with patch.object(
        scanner, "resolve_public", AsyncMock(return_value=["1.1.1.1"])
    ), patch.object(
        scanner,
        "tls_connect",
        AsyncMock(side_effect=ssl.SSLCertVerificationError("bad cert")),
    ):
        result = await scanner.probe_domain("example.com")
    assert not result["eligible"] and not result["certificate_ok"]


@pytest.mark.asyncio
async def test_parallel_limit_lock_and_release():
    running = peak = 0

    async def probe(domain):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {"domain": domain, "eligible": True, "latency_ms": 2}

    with patch.object(scanner, "probe_domain", probe), patch.object(
        scanner, "_scan_lock", asyncio.Lock()
    ):
        task = asyncio.create_task(
            scanner.scan_domains([f"x{i}.com" for i in range(12)])
        )
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            await scanner.scan_domains(["example.com"])
        await task
        assert peak == 3
        assert not scanner._scan_lock.locked()
        await scanner.scan_domains(["example.com"])


@pytest.mark.asyncio
async def test_panel_domain_is_never_probed():
    with patch.object(scanner, "probe_domain", AsyncMock()) as probe:
        result = await scanner.scan_domains(["panel.example.com"], "panel.example.com")
    probe.assert_not_called()
    assert not result["results"][0]["eligible"]


async def request(headers, body):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/v1/settings/protocols/sni/check", headers=headers, json=body
        )


@pytest.mark.asyncio
async def test_api_is_read_only_and_bounded(auth_headers):
    async with AsyncSessionLocal() as db:
        db.add(
            Setting(key="protocol.reality.server_name", value="original.example.com")
        )
        await db.commit()
    with patch.object(
        scanner,
        "probe_domain",
        AsyncMock(
            return_value={"domain": "example.com", "eligible": True, "latency_ms": 10}
        ),
    ):
        response = await request(auth_headers, {"domains": ["example.com"]})
    assert response.status_code == 200
    assert response.json()["vantage"] == "panel"
    async with AsyncSessionLocal() as db:
        assert (
            await db.get(Setting, "protocol.reality.server_name")
        ).value == "original.example.com"
    assert (
        await request(auth_headers, {"domains": ["example.com"] * 13})
    ).status_code == 422
    assert (
        await request(auth_headers, {"domains": ["http://example.com"]})
    ).status_code == 422


@pytest.mark.asyncio
async def test_api_requires_admin(auth_headers):
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        user.role = "subadmin"
        await db.commit()
    with patch.object(scanner, "probe_domain", AsyncMock()) as probe:
        response = await request(auth_headers, {"domains": ["example.com"]})
    assert response.status_code == 403
    assert (await request({}, {"domains": ["example.com"]})).status_code == 401
    probe.assert_not_called()


@pytest.mark.asyncio
async def test_real_tls13_handshake_and_http_without_external_network(
    tmp_path, monkeypatch
):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("example.com")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2", "http/1.1"])

    async def serve(reader, writer):
        try:
            request_bytes = await reader.read(4096)
            if request_bytes:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
        finally:
            await scanner.close_connection(writer)

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=context)
    port = server.sockets[0].getsockname()[1]
    original_connect = asyncio.open_connection

    async def local_connect(ip, target_port, **kwargs):
        assert ip == "1.1.1.1" and target_port == 443
        kwargs["ssl"].verify_flags |= ssl.VERIFY_X509_STRICT
        return await original_connect("127.0.0.1", port, **kwargs)

    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    try:
        with patch.object(
            scanner, "resolve_public", AsyncMock(return_value=["1.1.1.1"])
        ), patch.object(scanner.asyncio, "open_connection", local_connect):
            result = await scanner.probe_domain("example.com")
        assert result["eligible"] and result["alpn"] == "h2"
        assert result["tls_version"] == "TLSv1.3" and result["http_status"] == 200
    finally:
        server.close()
        await server.wait_closed()
