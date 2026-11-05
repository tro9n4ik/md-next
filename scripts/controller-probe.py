"""Ограниченная HTTPS-проверка по закреплённому IP с проверкой имени сертификата."""
import argparse
import http.client
import ipaddress
import json
import socket
import ssl
import subprocess
import sys
from urllib.parse import urlsplit


def probe(url, address):
    target = urlsplit(url)
    if target.scheme != "https" or not target.hostname or target.username or target.password:
        raise ValueError("Некорректный адрес контроллера")
    numeric = ipaddress.ip_address(address)
    context = ssl.create_default_context()
    class PinnedConnection(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.socket(socket.AF_INET6 if numeric.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(2.0)
            try:
                raw.connect((str(numeric), self.port))
                self.sock = context.wrap_socket(raw, server_hostname=self.host)
            except BaseException:
                raw.close()
                raise
    connection = PinnedConnection(target.hostname, target.port or 443, context=context, timeout=2.0)
    try:
        connection.request("GET", "/api/v1/health")
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError("Контроллер не готов")
        if json.loads(response.read(4096)) != {"status": "ok"}:
            raise RuntimeError("Некорректный ответ контроллера")
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        probe(args.url, args.ip)
        return
    try:
        result = subprocess.run([sys.executable, __file__, "--child", "--url", args.url, "--ip", args.ip],
                                capture_output=True, timeout=2.0)
        if result.returncode:
            raise RuntimeError("Проверка контроллера не прошла")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Истёк срок проверки контроллера (2 секунды)") from None


if __name__ == "__main__":
    main()
