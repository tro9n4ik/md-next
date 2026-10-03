"""Временный путь регистрации WARP через отдельный прокси выбранной ноды.

Запускается службой systemd с ограниченным временем жизни и ExecStopPost.
Перенаправляет только HTTPS-сокеты warp-svc; TLS остаётся сквозным.
"""

import argparse
import asyncio
import json
import ipaddress
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
import httpx

COMMENT = "md-next-warp-registration"
API_HOST = "api.cloudflareclient.com"


def cleanup() -> None:
    """Удалить только собственные правила, в том числе после аварийной остановки."""
    for binary in ("iptables", "ip6tables"):
        if not shutil.which(binary):
            continue
        result = subprocess.run([binary, "-w", "5", "-t", "nat", "-S", "OUTPUT"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            args = shlex.split(line)
            if args[:2] == ["-A", "OUTPUT"] and "--comment" in args and args[args.index("--comment") + 1] == COMMENT:
                subprocess.run([binary, "-w", "5", "-t", "nat", "-D", *args[1:]], check=True, timeout=10)


async def close_servers(servers, connections) -> None:
    """Сначала завершить соединения: wait_closed ожидает также их закрытия."""
    for server in servers:
        if server:
            server.close()
    pending = list(connections)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for server in servers:
        if server:
            await server.wait_closed()


def node_config(config: dict, node_id: int, port: int) -> dict:
    outbound = next((item for item in config.get("outbounds", []) if item.get("tag") == f"node-{node_id}"), None)
    if not outbound or outbound.get("protocol") not in {"trojan", "vless"}:
        raise RuntimeError("Нода отсутствует в действующей конфигурации Xray")
    if outbound.get("proxySettings") or outbound.get("streamSettings", {}).get("sockopt", {}).get("dialerProxy"):
        raise RuntimeError("Для регистрации нужна нода с самостоятельным выходом")
    return {"log": {"loglevel": "none"},
            "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"auth": "noauth", "udp": False}}],
            "outbounds": [outbound]}


async def socks_connection(port: int):
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)
    try:
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        if await reader.readexactly(2) != b"\x05\x00":
            raise RuntimeError("Нода не приняла SOCKS5")
        host = API_HOST.encode("ascii")
        writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + b"\x01\xbb")
        await writer.drain()
        reply = await reader.readexactly(4)
        if reply[:2] != b"\x05\x00":
            raise RuntimeError("Нода не открыла API Cloudflare")
        size = 4 if reply[3] == 1 else 16 if reply[3] == 4 else (await reader.readexactly(1))[0]
        await reader.readexactly(size + 2)
        return reader, writer
    except BaseException:
        writer.close()
        raise


async def register(node_id: int, config_path: Path) -> int:
    # Повторный запуск безопасен: подтверждённую регистрацию не заменяем.
    existing = await asyncio.create_subprocess_exec(shutil.which("warp-cli") or "/usr/bin/warp-cli", "--accept-tos", "registration", "show", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    if await asyncio.wait_for(existing.wait(), 10) == 0:
        print("Регистрация WARP уже существует.", flush=True)
        return 0
    if not Path("/sys/fs/cgroup/system.slice/warp-svc.service").is_dir():
        raise RuntimeError("Не найдена группа службы warp-svc")
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        proxy_port = port_socket.getsockname()[1]
    connections = set()
    xray = None
    server = None
    ipv6_server = None

    async def relay(reader, writer):
        task = asyncio.current_task()
        connections.add(task)
        remote_writer = None
        try:
            remote_reader, remote_writer = await asyncio.wait_for(socks_connection(proxy_port), 10)

            async def pump(source, target):
                while data := await source.read(65536):
                    target.write(data)
                    await target.drain()
                if target.can_write_eof():
                    target.write_eof()

            await asyncio.wait_for(asyncio.gather(pump(reader, remote_writer), pump(remote_reader, writer)), 40)
        except (OSError, RuntimeError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            if remote_writer:
                remote_writer.close()
            connections.discard(task)

    try:
        with tempfile.TemporaryDirectory(prefix="md-next-warp-") as directory:
            path = Path(directory) / "xray.json"
            path.write_text(json.dumps(node_config(json.loads(config_path.read_text()), node_id, proxy_port)), encoding="utf-8")
            path.chmod(0o600)
            xray = await asyncio.create_subprocess_exec(shutil.which("xray") or "/usr/local/bin/xray", "run", "-c", str(path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            for _ in range(30):
                if xray.returncode is not None:
                    raise RuntimeError("Временный прокси ноды не запустился")
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", proxy_port), .2)
                    writer.close()
                    break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                raise RuntimeError("Временный прокси ноды не ответил")
            server = await asyncio.start_server(relay, "127.0.0.1", 0)
            relay_port = server.sockets[0].getsockname()[1]
            addresses = {(socket.AF_INET, "162.159.137.105"), (socket.AF_INET, "162.159.138.105")}
            for info in await asyncio.get_running_loop().getaddrinfo(API_HOST, 443, type=socket.SOCK_STREAM):
                addresses.add((info[0], info[4][0]))
            # У системного DNS и внутреннего резолвера WARP могут быть разные
            # ответы. Дополняем адреса DNS-ответом, полученным через саму ноду.
            async with httpx.AsyncClient(proxy=f"socks5://127.0.0.1:{proxy_port}", timeout=8, trust_env=False) as client:
                for record_type in ("A", "AAAA"):
                    try:
                        response = await client.get("https://cloudflare-dns.com/dns-query", params={"name": API_HOST, "type": record_type}, headers={"Accept": "application/dns-json"})
                        response.raise_for_status()
                        for answer in response.json().get("Answer", []):
                            if answer.get("type") in (1, 28):
                                ip = ipaddress.ip_address(answer["data"])
                                addresses.add((socket.AF_INET6 if ip.version == 6 else socket.AF_INET, str(ip)))
                    except (httpx.HTTPError, ValueError, KeyError):
                        pass
            # Порт слушает IPv4. Для IPv6 используем отдельный локальный слушатель.
            ipv6_server = None
            if any(family == socket.AF_INET6 for family, _ in addresses):
                try:
                    ipv6_server = await asyncio.start_server(relay, "::1", relay_port, family=socket.AF_INET6)
                except OSError:
                    # На серверах с выключенным IPv6 служба также не может
                    # использовать такие назначения; остаются правила IPv4.
                    addresses = {(family, address) for family, address in addresses if family == socket.AF_INET}
            try:
                for family, address in sorted(addresses):
                    binary = "ip6tables" if family == socket.AF_INET6 else "iptables"
                    subprocess.run([binary, "-w", "5", "-t", "nat", "-I", "OUTPUT", "1", "-p", "tcp", "--dport", "443", "-d", address,
                                    "-m", "cgroup", "--path", "system.slice/warp-svc.service", "-m", "comment", "--comment", COMMENT,
                                    "-j", "REDIRECT", "--to-ports", str(relay_port)], check=True, capture_output=True, timeout=10)
                process = await asyncio.create_subprocess_exec(shutil.which("warp-cli") or "/usr/bin/warp-cli", "--accept-tos", "registration", "new", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                code = await asyncio.wait_for(process.wait(), 30)
                print("Регистрация WARP через ноду завершена." if code == 0 else "Cloudflare отклонил регистрацию через ноду.", flush=True)
                return code
            finally:
                if ipv6_server:
                    ipv6_server.close()
    finally:
        cleanup()
        await close_servers((server, ipv6_server), connections)
        if xray and xray.returncode is None:
            xray.terminate()
            try:
                await asyncio.wait_for(xray.wait(), 3)
            except asyncio.TimeoutError:
                xray.kill()
                await xray.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Регистрация WARP через ноду")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--node-id", type=int)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    if args.cleanup:
        cleanup()
    else:
        try:
            raise SystemExit(asyncio.run(register(args.node_id, args.config)))
        except Exception as exc:
            print("Не удалось зарегистрировать WARP через ноду: " + type(exc).__name__, flush=True)
            raise SystemExit(1)
