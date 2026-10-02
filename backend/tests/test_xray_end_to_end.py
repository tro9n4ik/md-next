import base64, json, os, socket, ssl, select, subprocess, threading, time, uuid, shutil, ipaddress
import pytest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import ExitStack
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, x25519
from cryptography.x509.oid import NameOID
from datetime import datetime, timedelta, timezone
from app.services.xray import XrayService


@pytest.mark.skipif(
    not shutil.which("xray") or not shutil.which("curl"),
    reason="Requires Xray and curl",
)
@pytest.mark.parametrize("kind", ["reality", "hysteria"])
@pytest.mark.parametrize("mode", ["http", "https", "udp"])
def test_real_client_master_node_roundtrip(tmp_path, monkeypatch, kind, mode):
    """Local real binaries; a relay emulates Nginx's incoming PROXY header."""
    monkeypatch.setenv("NODE_PROBE_ENABLED", "false")
    root = tmp_path
    xray = shutil.which("xray")

    def port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(label, config):
        f = root / (label + ".json")
        f.write_text(json.dumps(config))
        log = (root / (label + ".log")).open("w")
        p = subprocess.Popen([xray, "run", "-config", str(f)], stdout=log, stderr=log)
        procs.append(p)
        return p

    procs = []
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("example.test"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            False,
        )
        .sign(key, hashes.SHA256())
    )
    (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (root / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(root / "cert.pem", root / "key.pem")
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    target_port = port()
    target = socket.socket()
    target.bind(("127.0.0.1", target_port))
    target.listen()

    def target_client(c):
        try:
            c.settimeout(8)
            h = b""
            while not h.endswith(b"\r\n"):
                chunk = c.recv(1)
                if not chunk:
                    return
                h += chunk
            if not h.startswith(b"PROXY "):
                return
            c = ctx.wrap_socket(c, server_side=True)
            c.recv(4096)
        except Exception:
            pass
        finally:
            c.close()

    def accept_target():
        while True:
            try:
                c, _ = target.accept()
            except OSError:
                return
            threading.Thread(target=target_client, args=(c,), daemon=True).start()

    threading.Thread(target=accept_target, daemon=True).start()

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            data = self.client_address[0].encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    if mode == "https":
        origin_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        origin_ctx.load_cert_chain(root / "cert.pem", root / "key.pem")
        origin.socket = origin_ctx.wrap_socket(origin.socket, server_side=True)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    echo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    echo.bind(("127.0.0.1", 0))

    def udp_echo():
        while True:
            try:
                data, peer = echo.recvfrom(4096)
                echo.sendto(peer[0].encode() + b":" + data, peer)
            except OSError:
                return

    threading.Thread(target=udp_echo, daemon=True).start()
    node_port = port()
    master_port = port()
    hy_port = port()
    master_socks = port()
    api_port = port()
    proxy_port = port()
    node = {
        "id": 1,
        "host": "127.0.0.1",
        "port": node_port,
        "protocol": "trojan",
        "secret": "node-secret",
        "is_enabled": True,
    }
    from types import SimpleNamespace

    rkey = x25519.X25519PrivateKey.generate()
    priv = (
        base64.urlsafe_b64encode(
            rkey.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        )
        .decode()
        .rstrip("=")
    )
    pub = (
        base64.urlsafe_b64encode(
            rkey.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )
        .decode()
        .rstrip("=")
    )
    uid = str(uuid.uuid4())
    master = json.loads(
        XrayService.generate_config(
            [{"id": uid, "flow": "xtls-rprx-vision", "email": "test"}],
            server_private_key=priv,
            server_name="example.test",
            dest=f"127.0.0.1:{target_port}",
            active_node=SimpleNamespace(**node),
            profile_options={
                "enabled": {"vless_reality_tcp", "hysteria2"},
                "hysteria2_clients": [{"auth": "hy-secret", "email": "hy-test"}],
                "hysteria2_port": hy_port,
                "tls_cert": str(root / "cert.pem"),
                "tls_key": str(root / "key.pem"),
                "node_fallback_tag": "block",
            },
        )
    )
    for i in master["inbounds"]:
        if i.get("port") == 8444:
            i["port"] = master_port
            i["streamSettings"]["realitySettings"]["xver"] = 1
        elif i.get("port") == 10808:
            i["port"] = master_socks
        elif i.get("port") == 10085:
            i["port"] = api_port
    master["log"] = {"loglevel": "debug"}
    node_config = {
        "log": {"loglevel": "debug"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": node_port,
                "protocol": "trojan",
                "settings": {"clients": [{"password": "node-secret"}]},
                "streamSettings": {
                    "network": "grpc",
                    "security": "none",
                    "grpcSettings": {"serviceName": "MD-Next-Node"},
                },
            }
        ],
        "outbounds": [
            {"protocol": "freedom", "sendThrough": "127.0.0.2", "settings": {}}
        ],
    }
    relay = socket.socket()
    relay.bind(("127.0.0.1", proxy_port))
    relay.listen()

    def relay_client(c):
        u = None
        try:
            u = socket.create_connection(("127.0.0.1", master_port))
            u.sendall(b"PROXY TCP4 127.0.0.1 127.0.0.1 12345 443\r\n")
            while True:
                ready, _, _ = select.select([c, u], [], [], 10)
                if not ready:
                    return
                for a in ready:
                    d = a.recv(65536)
                    if not d:
                        return
                    (u if a is c else c).sendall(d)
        except Exception:
            pass
        finally:
            c.close()
            if u:
                u.close()

    def accept_relay():
        while True:
            try:
                c, _ = relay.accept()
            except OSError:
                return
            threading.Thread(target=relay_client, args=(c,), daemon=True).start()

    threading.Thread(target=accept_relay, daemon=True).start()
    try:
        start("node", node_config)
        start("master", master)
        for kind in [kind]:
            socks = port()
            if kind == "reality":
                outbound = {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": "127.0.0.1",
                                "port": proxy_port,
                                "users": [
                                    {
                                        "id": uid,
                                        "encryption": "none",
                                        "flow": "xtls-rprx-vision",
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": "example.test",
                            "fingerprint": "chrome",
                            "publicKey": pub,
                            "shortId": "",
                        },
                    },
                }
            else:
                outbound = {
                    "protocol": "hysteria",
                    "settings": {"version": 2, "address": "127.0.0.1", "port": hy_port},
                    "streamSettings": {
                        "network": "hysteria",
                        "security": "tls",
                        "tlsSettings": {
                            "serverName": "example.test",
                            "certificates": [
                                {
                                    "certificateFile": str(root / "cert.pem"),
                                    "usage": "verify",
                                }
                            ],
                            "alpn": ["h3"],
                        },
                        "hysteriaSettings": {"version": 2, "auth": "hy-secret"},
                    },
                }
            p = start(
                kind,
                {
                    "log": {"loglevel": "debug"},
                    "inbounds": [
                        {
                            "listen": "127.0.0.1",
                            "port": socks,
                            "protocol": "socks",
                            "settings": {"auth": "noauth", "udp": True},
                        }
                    ],
                    "outbounds": [outbound],
                },
            )
            time.sleep(0.6)
            assert p.poll() is None, (root / (kind + ".log")).read_text()
            if mode in ("http", "https"):
                result = subprocess.run(
                    [
                        "curl",
                        "--noproxy",
                        "",
                        "-sS",
                        "--max-time",
                        "8",
                        "--cacert",
                        str(root / "cert.pem"),
                        "--socks5-hostname",
                        f"127.0.0.1:{socks}",
                        f"{mode}://127.0.0.1:{origin.server_port}/",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0, result.stderr
                assert (
                    result.stdout == "127.0.0.2"
                ), "Must use the node, not the master direct route"
            else:
                with socket.create_connection(
                    ("127.0.0.1", socks), timeout=5
                ) as control:
                    control.sendall(b"\x05\x01\x00")
                    assert control.recv(2) == b"\x05\x00"
                    control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
                    reply = control.recv(10)
                    assert reply[:4] == b"\x05\x00\x00\x01"
                    endpoint = (
                        socket.inet_ntoa(reply[4:8]),
                        int.from_bytes(reply[8:10], "big"),
                    )
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                        udp.settimeout(5)
                        payload = b"network-dns-check"
                        udp.sendto(
                            b"\x00\x00\x00\x01"
                            + socket.inet_aton("127.0.0.1")
                            + echo.getsockname()[1].to_bytes(2, "big")
                            + payload,
                            endpoint,
                        )
                        response, _ = udp.recvfrom(4096)
                        assert response[10:] == b"127.0.0.2:" + payload
            p.terminate()
            p.wait(timeout=5)
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        relay.close()
        target.close()
        echo.close()
        origin.shutdown()
        origin.server_close()
