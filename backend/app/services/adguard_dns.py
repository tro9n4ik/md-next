"""Optional authenticated DNS-over-HTTPS bridge to loopback AdGuard Home."""
import asyncio
import base64
import time
from collections import OrderedDict, deque

import httpx
from fastapi import HTTPException, Request

_MAX_WIRE = 4096
_slots = asyncio.Semaphore(32)
_rates: OrderedDict[int, deque] = OrderedDict()


def validate_query(wire: bytes) -> bytes:
    if not 12 <= len(wire) <= _MAX_WIRE:
        raise HTTPException(400, "Некорректный DNS-запрос")
    flags = int.from_bytes(wire[2:4], "big")
    if flags & 0xF800 or int.from_bytes(wire[4:6], "big") != 1:
        raise HTTPException(400, "Поддерживается один обычный DNS-запрос")
    return wire


async def read_query(request: Request) -> bytes:
    if request.method == "GET":
        encoded = request.query_params.get("dns", "")
        if len(encoded) > 5500:
            raise HTTPException(413, "DNS-запрос слишком большой")
        try:
            return validate_query(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        except ValueError as exc:
            raise HTTPException(400, "Некорректный DNS-запрос") from exc
    if request.headers.get("content-type", "").split(";")[0] != "application/dns-message":
        raise HTTPException(415, "Ожидается application/dns-message")
    wire = bytearray()
    async for chunk in request.stream():
        wire.extend(chunk)
        if len(wire) > _MAX_WIRE:
            raise HTTPException(413, "DNS-запрос слишком большой")
    return validate_query(bytes(wire))


async def resolve_query(client_id: int, filtered: bool, wire: bytes) -> bytes:
    now = time.monotonic()
    stamps = _rates.setdefault(client_id, deque())
    _rates.move_to_end(client_id)
    while stamps and stamps[0] <= now - 60:
        stamps.popleft()
    if len(stamps) >= 600:
        raise HTTPException(429, "Слишком много DNS-запросов")
    stamps.append(now)
    while len(_rates) > 4096:
        _rates.popitem(last=False)
    upstream = f"http://127.0.0.1:3001/dns-query/c-{client_id}" if filtered else "https://cloudflare-dns.com/dns-query"
    try:
        await asyncio.wait_for(_slots.acquire(), timeout=1)
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
                response = await http.post(
                    upstream,
                    content=wire, headers={"Content-Type": "application/dns-message"},
                )
                response.raise_for_status()
                result = response.content
                if not 12 <= len(result) <= 65535 or result[:2] != wire[:2] or not result[2] & 0x80:
                    raise ValueError("Invalid DNS response")
                return result
        finally:
            _slots.release()
    except (asyncio.TimeoutError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "DNS-фильтр временно недоступен") from exc
