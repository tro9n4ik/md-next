"""Actual KedrLink installer/systemd lifecycle on a disposable hosted CI VM only.

The Xray/WARP stages are deliberately not run. The real join script functions
build the SDK-verified agent, register over TLS, install it, and switch it off.
"""
import asyncio
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
from unittest.mock import AsyncMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import uvicorn

from app.main import app
from test_subadmins import request


async def command(*args, timeout=240, check=True, environment=None):
    process = await asyncio.create_subprocess_exec(*map(str,args), env=environment,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(),timeout)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    output = (stdout+stderr).decode(errors="replace")
    if check and process.returncode:
        raise AssertionError(f"Command {args[0]} failed ({process.returncode}): {output[-3000:]}")
    return process.returncode, output


@pytest.mark.asyncio
async def test_actual_installer_and_systemd(auth_headers, tmp_path, monkeypatch):
    if os.environ.get("MDNEXT_RUN_SYSTEMD_INSTALL_TEST") != "1":
        pytest.skip("explicit disposable hosted CI opt-in required")
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    assert os.geteuid() == 0 and Path("/proc/1/comm").read_text().strip() == "systemd"
    assert not Path("/opt/md-next/backend/.env").exists(), "never run on an installed panel"
    unit = Path("/etc/systemd/system/kedrlink-agent.service")
    binary = Path("/usr/local/bin/kedrlink-agent")
    config = Path("/etc/md-next/kedrlink-agent.json")
    ca = Path("/usr/local/share/ca-certificates/kedrlink-installer-ci.crt")
    install_log = Path("/tmp/md-next-node-install.log")
    for file in (unit,binary,config,ca,install_log):
        assert not file.exists(), f"refuse to overwrite {file}"
    directory_existed = config.parent.exists()
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,"KedrLink installer CI")])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=1))
                   .not_valid_after(now+dt.timedelta(hours=1))
                   .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),critical=False)
                   .sign(key,hashes.SHA256()))
    cert_file, key_file = tmp_path/"cert.pem", tmp_path/"key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    listener = socket.socket()
    listener.bind(("127.0.0.1",0))
    port = listener.getsockname()[1]
    panel = f"https://127.0.0.1:{port}"
    monkeypatch.setenv("PANEL_PUBLIC_URL",panel)
    server = uvicorn.Server(uvicorn.Config(app,lifespan="off",log_level="error",
                                         ssl_certfile=str(cert_file),ssl_keyfile=str(key_file)))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        ca.write_bytes(cert_file.read_bytes())
        await command("update-ca-certificates")
        for _ in range(100):
            if server.started: break
            if serving.done(): await serving
            await asyncio.sleep(0.02)
        assert server.started

        async def install(transport):
            result = await request("POST","/api/v1/nodes/invites",auth_headers,
                                   json={"name":"Installer CI","transport":transport})
            assert result.status_code == 200
            token = result.json()["token"]
            rendered = await request("GET","/api/v1/nodes/join",{},params={"token":token})
            assert rendered.status_code == 200
            # Execute the real agent functions, not Xray/WARP installation.
            prefix, marker, _ = rendered.text.partition('run_step "Подготовка системных зависимостей"')
            assert marker
            script = tmp_path/f"join-{transport}.sh"
            script.write_text(prefix+'''
curl() {
  case "${*: -1}" in
    https://api.ipify.org) printf '192.0.2.77';;
    *) command curl "$@";;
  esac
}
export NODE_PUBLIC_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
export NODE_IDENTITY=e069b4f9-c397-4df0-94af-d78d77b4ac94
export NODE_SHORT_ID=0123456789abcdef
run_step 'Build KedrLink' prepare_kedrlink
if [ "$TRANSPORT_INJECTED" = xray ] && [ -n "$KEDRLINK_BUILD_DIR" ]; then exit 1; fi
run_step 'Register through verified HTTPS' register_node
run_step 'Install chosen control channel' install_kedrlink
''',encoding="utf-8")
            with patch("app.api.nodes.ClientService.sync_xray_clients",new=AsyncMock(return_value=(True,"test data-plane stub"))):
                _, output = await command("bash",script)
            assert token not in output
            return output

        await install("mdnt")
        credentials = json.loads(config.read_text())
        token = credentials["token"]
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert stat.S_IMODE(binary.stat().st_mode) == 0o755
        await command("systemd-analyze","verify",unit)
        _, properties = await command("systemctl","show","kedrlink-agent.service",
                                      "-p","MainPID","-p","ActiveState","-p","NoNewPrivileges","-p","ProtectSystem")
        settings = dict(line.split("=",1) for line in properties.splitlines() if "=" in line)
        assert settings["ActiveState"] == "active"
        assert settings["NoNewPrivileges"] == "yes" and settings["ProtectSystem"] == "strict"
        original_pid = int(settings["MainPID"])
        path = credentials["poll_url"].removeprefix(panel).removesuffix("/poll")

        async def status_command():
            queued = await request("POST",path+"/commands",auth_headers,json={"kind":"status"})
            assert queued.status_code == 202
            command_id = queued.json()["command"]["id"]
            async def completed():
                while True:
                    state = (await request("GET",path,auth_headers)).json()
                    if state["command"]["status"] == "completed":
                        assert state["command"]["id"] == command_id
                        assert state["state"] == "online"
                        return
                    await asyncio.sleep(0.2)
            await asyncio.wait_for(completed(),30)

        await status_command()
        await command("systemctl","restart","kedrlink-agent.service")
        _, pid = await command("systemctl","show","kedrlink-agent.service","--property=MainPID","--value")
        assert int(pid.strip()) > 0 and int(pid.strip()) != original_pid
        await status_command()
        await command("systemctl","stop","kedrlink-agent.service")

        wrong = tmp_path/"wrong-key.json"
        wrong.write_text(json.dumps({**credentials,"token":"0"*64}))
        wrong.chmod(0o600)
        code, output = await command(binary,"-config",wrong,"-once",check=False)
        assert code != 0 and "HTTP 401" in output
        assert token not in output
        ca.unlink()
        await command("update-ca-certificates")
        code, output = await command(binary,"-config",config,"-once",check=False)
        assert code != 0 and "connection failed" in output
        assert token not in output
        ca.write_bytes(cert_file.read_bytes())
        await command("update-ca-certificates")
        await command("systemctl","start","kedrlink-agent.service")
        await status_command()

        standard_output = await install("xray")
        assert "go.tar.gz" not in standard_output
        assert (await command("systemctl","is-active","--quiet","kedrlink-agent.service",check=False))[0] != 0
        assert (await command("systemctl","is-enabled","--quiet","kedrlink-agent.service",check=False))[0] != 0
        assert (await request("GET",path,auth_headers)).json()["state"] == "standard"
        rejected = await request("POST",path+"/poll",{"Authorization":"Bearer "+token},
                                 json={"version":1,"snapshot":{"agent_version":"0.1.0","uptime_ms":1}})
        assert rejected.status_code == 401
        assert token not in install_log.read_text()
    finally:
        await command("systemctl","disable","--now","kedrlink-agent.service",check=False)
        for file in (unit,binary,config,ca,install_log,Path("/usr/local/bin/.kedrlink-agent.next")):
            file.unlink(missing_ok=True)
        if not directory_existed and config.parent.exists():
            config.parent.rmdir()
        await command("systemctl","daemon-reload")
        await command("update-ca-certificates")
        server.should_exit = True
        await asyncio.wait_for(serving,5)
        listener.close()
