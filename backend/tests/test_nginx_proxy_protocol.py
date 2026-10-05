import os
import re
import shutil
import subprocess
import time
import pytest
import socket
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        real_ip = self.headers.get("X-Real-IP", self.client_address[0])
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = json.dumps({"client_ip": real_ip, "path": self.path})
        self.wfile.write(response.encode("utf-8"))

    def log_message(self, format, *args):
        pass

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def extract_nginx_configs_from_installer(install_script_path):
    with open(install_script_path, "r", encoding="utf-8") as f:
        content = f.read()

    stream_match = re.search(
        r"cat <<EOF > /etc/nginx/stream-available/md-next-stream\.conf\n(.*?)\nEOF",
        content,
        re.DOTALL
    )
    site_match = re.search(
        r"cat <<EOF > /etc/nginx/sites-available/md-next\.conf\n(.*?)\nEOF",
        content,
        re.DOTALL
    )

    if not stream_match or not site_match:
        raise ValueError("Не удалось найти heredoc-конфигурации Nginx в scripts/install.sh")

    stream_conf = stream_match.group(1).replace(r"\$", "$")
    site_conf = site_match.group(1).replace(r"\$", "$")

    return stream_conf, site_conf

@pytest.mark.skipif(shutil.which("nginx") is None, reason="Nginx не найден на хосте")
def test_nginx_proxy_protocol(tmp_path):
    install_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../scripts/install.sh"))
    if not os.path.exists(install_script):
        pytest.skip("Скрипт scripts/install.sh не найден")

    try:
        raw_stream_conf, raw_site_conf = extract_nginx_configs_from_installer(install_script)
    except ValueError as e:
        pytest.fail(str(e))

    stream_port = find_free_port()
    fake_port, panel_port, http_port = [find_free_port() for _ in range(3)]

    # Use isolated ports so the test can run alongside a live installation.
    echo_server = HTTPServer(("127.0.0.1", 0), EchoHandler)
    echo_port = echo_server.server_address[1]
    server_thread = threading.Thread(target=echo_server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    main_domain = "example.com"
    panel_domain = "panel.example.com"

    # Создание временных директорий для конфигов, логов и SSL
    ssl_dir = tmp_path / "ssl"
    www_dir = tmp_path / "www"
    ssl_dir.mkdir()
    www_dir.mkdir()

    fake_html = www_dir / "fake"
    fake_html.mkdir()
    (fake_html / "index.html").write_text("Fake Site Content")

    panel_html = www_dir / "panel"
    panel_html.mkdir()
    (panel_html / "index.html").write_text("Panel Static Content")
    # Default nginx workers may run as any unprivileged user. Give them access
    # only to these public fixture files, including the pytest parent folders.
    for directory in (tmp_path.parent.parent, tmp_path.parent, tmp_path, ssl_dir, www_dir, fake_html, panel_html):
        directory.chmod(directory.stat().st_mode | 0o005)

    cert_path = ssl_dir / "fullchain.pem"
    key_path = ssl_dir / "privkey.pem"

    # Генерация SSL сертификатов
    subprocess.run([
        "openssl", "req", "-x509", "-nodes", "-days", "1",
        "-newkey", "rsa:2048",
        "-keyout", str(key_path),
        "-out", str(cert_path),
        "-subj", f"/CN={main_domain}",
        "-addext", f"subjectAltName=DNS:{main_domain},DNS:{panel_domain}"
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cert_path.chmod(0o644)
    key_path.chmod(0o644)  # Disposable self-signed key, never a production key.

    # Модуль stream для Debian/Ubuntu
    load_module_directive = ""
    module_so = "/usr/lib/nginx/modules/ngx_stream_module.so"
    if os.path.exists(module_so):
        load_module_directive = f"load_module {module_so};\n"

    # Подстановка параметров в stream_conf из install.sh
    rendered_stream = raw_stream_conf.replace("$MAIN_DOMAIN", main_domain).replace("$PANEL_DOMAIN", panel_domain)
    rendered_stream = re.sub(r"listen\s+443;", f"listen 127.0.0.1:{stream_port};", rendered_stream)
    rendered_stream = rendered_stream.replace('127.0.0.1:8080', f'127.0.0.1:{fake_port}').replace('127.0.0.1:8443', f'127.0.0.1:{panel_port}')

    # Подстановка параметров в site_conf из install.sh
    rendered_site = raw_site_conf.replace("$MAIN_DOMAIN", main_domain).replace("$PANEL_DOMAIN", panel_domain)
    rendered_site = rendered_site.replace('127.0.0.1:8000', f'127.0.0.1:{echo_port}')
    rendered_site = rendered_site.replace('127.0.0.1:8080', f'127.0.0.1:{fake_port}').replace('127.0.0.1:8443', f'127.0.0.1:{panel_port}')
    rendered_site = re.sub(r'listen\s+80;', f'listen 127.0.0.1:{http_port};', rendered_site)
    rendered_site = rendered_site.replace("/etc/nginx/ssl_dummy/fullchain.pem", str(cert_path))
    rendered_site = rendered_site.replace("/etc/nginx/ssl_dummy/privkey.pem", str(key_path))
    rendered_site = rendered_site.replace("/opt/md-next/backend/app/static/fake/", str(fake_html) + "/")
    rendered_site = rendered_site.replace("/opt/md-next/frontend/dist/", str(panel_html) + "/")

    nginx_conf = tmp_path / "nginx.conf"
    nginx_conf.write_text(f"""
{load_module_directive}worker_processes 1;
pid {tmp_path}/nginx.pid;
error_log {tmp_path}/error.log debug;

events {{
    worker_connections 1024;
}}

stream {{
{rendered_stream}
}}

http {{
    access_log {tmp_path}/access.log;
{rendered_site}
}}
""")

    # Валидация конфига nginx -t перед запуском
    test_proc = subprocess.run(["nginx", "-t", "-c", str(nginx_conf)], capture_output=True, text=True)
    if test_proc.returncode != 0:
        if "unknown directive \"stream\"" in test_proc.stderr:
            echo_server.shutdown()
            pytest.skip("Nginx на машине не имеет встроенного или загруженного модуля stream")
        else:
            echo_server.shutdown()
            pytest.fail(f"Nginx test config failed: {test_proc.stderr}")

    proc = subprocess.Popen(["nginx", "-c", str(nginx_conf), "-g", "daemon off;"])
    time.sleep(1)

    try:
        # Проверка запроса с interface 127.0.0.2
        cmd2 = [
            "curl", "-s", "-k",
            "--interface", "127.0.0.2",
            "--resolve", f"{panel_domain}:{stream_port}:127.0.0.1",
            f"https://{panel_domain}:{stream_port}/api/test"
        ]
        res2 = subprocess.run(cmd2, capture_output=True, text=True, check=True)
        data2 = json.loads(res2.stdout)
        assert data2["client_ip"] == "127.0.0.2"

        # Проверка запроса с interface 127.0.0.3
        cmd3 = [
            "curl", "-s", "-k",
            "--interface", "127.0.0.3",
            "--resolve", f"{panel_domain}:{stream_port}:127.0.0.1",
            f"https://{panel_domain}:{stream_port}/api/test"
        ]
        res3 = subprocess.run(cmd3, capture_output=True, text=True, check=True)
        data3 = json.loads(res3.stdout)
        assert data3["client_ip"] == "127.0.0.3"

        # Запрос на чужой SNI должен отдавать заглушку
        cmd_fake = [
            "curl", "-s", "-k",
            "--resolve", f"other.domain.com:{stream_port}:127.0.0.1",
            f"https://other.domain.com:{stream_port}/"
        ]
        res_fake = subprocess.run(cmd_fake, capture_output=True, text=True, check=True)
        assert "Fake Site Content" in res_fake.stdout

        # Статика панели должна отдаваться по HTTP/2
        cmd_h2 = [
            "curl", "-s", "-k", "-I",
            "--http2",
            "--resolve", f"{panel_domain}:{stream_port}:127.0.0.1",
            f"https://{panel_domain}:{stream_port}/"
        ]
        res_h2 = subprocess.run(cmd_h2, capture_output=True, text=True, check=True)
        assert "HTTP/2 200" in res_h2.stdout or "HTTP/2" in res_h2.stdout

    finally:
        proc.terminate()
        proc.wait()
        echo_server.shutdown()
