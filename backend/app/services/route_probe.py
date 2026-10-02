"""Проверка фактического внешнего IP через текущий маршрут Xray."""

import ipaddress

import httpx


TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
XRAY_SOCKS_PROXY = "socks5://127.0.0.1:10808"


async def probe_current_exit() -> dict[str, str | None]:
    """Возвращает фактический внешний IP, используя локальный SOCKS-вход Xray."""
    async with httpx.AsyncClient(
        proxy=XRAY_SOCKS_PROXY,
        timeout=httpx.Timeout(10.0, connect=4.0),
        trust_env=False,
    ) as client:
        response = await client.get(TRACE_URL)
        response.raise_for_status()

    values = {}
    for line in response.text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value

    address = values.get("ip", "")
    try:
        ipaddress.ip_address(address)
    except ValueError as exc:
        raise RuntimeError("Сервис проверки не вернул корректный внешний IP") from exc

    return {"ip": address, "country": values.get("loc"), "warp": values.get("warp")}
