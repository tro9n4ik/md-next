"""Проверка WARP ноды через временный Xray без изменения рабочих маршрутов."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile

from app.services.shell import find_command
from app.services.node_transport import node_outbound as build_node_outbound


def warp_outbound(port: int, node_tag: str | None = None) -> dict:
    outbound = {"tag": "warp", "protocol": "socks", "settings": {
        "servers": [{"address": "127.0.0.1", "port": port}]}}
    if node_tag:
        outbound["streamSettings"] = {"sockopt": {"dialerProxy": node_tag}}
    return outbound


async def test_node_proxy(node, port: int, probe) -> dict:
    if not node or not node.is_enabled or not node.secret:
        raise ValueError("Нода WARP отсутствует, отключена или не имеет секрета")
    binary = find_command("xray")
    if not binary:
        raise RuntimeError("Xray не установлен: невозможно проверить WARP ноды")
    tag = f"node-{node.id}"
    node_outbound = build_node_outbound({
        "id": node.id, "host": node.host, "port": node.port,
        "protocol": node.protocol, "secret": node.secret,
        "public_key": getattr(node, "public_key", None),
    })
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        local_port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="md-next-warp-check-") as directory:
        os.chmod(directory, 0o700)
        path = Path(directory) / "config.json"
        path.write_text(json.dumps({"log": {"loglevel": "none"}, "inbounds": [{
            "listen": "127.0.0.1", "port": local_port, "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False}}],
            "outbounds": [warp_outbound(port, tag), node_outbound]}), encoding="utf-8")
        path.chmod(0o600)
        process = await asyncio.create_subprocess_exec(binary, "run", "-config", str(path),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            for _ in range(30):
                if process.returncode is not None:
                    raise RuntimeError("Не удалось запустить проверку WARP ноды")
                try:
                    _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", local_port), 0.2)
                    writer.close()
                    await writer.wait_closed()
                    break
                except (OSError, TimeoutError):
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Проверочный прокси Xray не запустился")
            return await probe(local_port, timeout=8.0)
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
