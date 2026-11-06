"""Real Go agent -> verified TLS -> FastAPI -> persistent command ACK."""
import asyncio
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import socket

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import uvicorn

from app.main import app
from test_kedrlink import register_agent
from test_subadmins import request


@pytest.mark.asyncio
async def test_real_agent_over_verified_https(auth_headers, tmp_path):
    binary = os.environ.get("MDNEXT_TEST_KEDRLINK_BINARY")
    if not binary:
        pytest.skip("set MDNEXT_TEST_KEDRLINK_BINARY to the built Linux agent")
    assert Path(binary).is_file()
    data = await register_agent(auth_headers)
    path = f'/api/v1/nodes/{data["node_id"]}/control'
    queued = await request("POST", path+"/commands", auth_headers, json={"kind": "status"})
    assert queued.status_code == 202
    command_id = queued.json()["command"]["id"]
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KedrLink integration test")])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=1))
                   .not_valid_after(now+dt.timedelta(hours=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path/"cert.pem", tmp_path/"key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    listener = socket.socket()
    listener.bind(("127.0.0.1",0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error",
                                         ssl_certfile=str(cert_path), ssl_keyfile=str(key_path)))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        for _ in range(100):
            if server.started: break
            if serving.done(): await serving
            await asyncio.sleep(0.02)
        assert server.started
        config = tmp_path/"agent.json"
        config.write_text(json.dumps({"poll_url": f"https://127.0.0.1:{port}"+path+"/poll", "token": data["control"]["token"]}))
        config.chmod(0o600)
        environment = {**os.environ, "SSL_CERT_FILE": str(cert_path), "SSL_CERT_DIR": str(tmp_path)}
        process = await asyncio.create_subprocess_exec(binary, "-config", str(config), env=environment,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        async def completed():
            while True:
                state = (await request("GET", path, auth_headers)).json()
                if state["command"]["status"] == "completed": return state
                if process.returncode is not None: raise AssertionError("Go agent exited before acknowledgement")
                await asyncio.sleep(0.1)
        state = await asyncio.wait_for(completed(),10)
        assert state["state"] == "online"
        assert state["agent_version"] == "0.1.0"
        assert state["command"]["id"] == command_id
        assert state["command"]["result"]["uptime_ms"] >= 0
        process.terminate()
        assert await asyncio.wait_for(process.wait(),3) == 0
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if process and process.stderr:
            errors = (await process.stderr.read()).decode(errors="replace")
            assert data["control"]["token"] not in errors
        server.should_exit = True
        await asyncio.wait_for(serving,5)
        listener.close()
