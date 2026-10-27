"""Bounded, read-only Reality target checks from the ingress server."""

import asyncio
import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone

DEFAULT_DOMAINS = (
    "www.microsoft.com",
    "www.apple.com",
    "www.bing.com",
    "www.wikipedia.org",
)
MAX_DOMAINS = 12
SCAN_TIMEOUT = 10
_scan_lock = asyncio.Lock()


def normalize_domains(domains: list[str]) -> list[str]:
    if not 1 <= len(domains) <= MAX_DOMAINS:
        raise ValueError(f"Укажите от 1 до {MAX_DOMAINS} доменов")
    result = []
    for value in domains:
        try:
            domain = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Некорректное имя домена") from exc
        if (
            len(domain) > 253
            or "." not in domain
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in domain.split(".")
            )
        ):
            raise ValueError("Укажите домены без протокола, порта и пути")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError("Нужен домен для SNI, а не IP-адрес")
        if domain.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
            raise ValueError("Укажите публичный домен")
        if domain not in result:
            result.append(domain)
    return result


async def resolve_public(domain: str) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        domain, 443, type=socket.SOCK_STREAM
    )
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(
        not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
        for ip in addresses
    ):
        raise ValueError("DNS содержит непубличный адрес; проверка запрещена")
    return addresses


async def tls_connect(ip: str, domain: str, alpn: list[str]):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(alpn)
    return await asyncio.open_connection(
        ip,
        443,
        ssl=context,
        server_hostname=domain,
        ssl_handshake_timeout=3,
        limit=8192,
    )


async def close_connection(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
    except (OSError, asyncio.TimeoutError):
        pass


async def probe_domain(domain: str) -> dict:
    result = dict(
        domain=domain,
        target=f"{domain}:443",
        eligible=False,
        ip=None,
        tls_version=None,
        alpn=None,
        certificate_ok=False,
        latency_ms=None,
        http_status=None,
        reason="",
    )

    async def run():
        addresses = await resolve_public(domain)
        # Prefer IPv4; keep the resolved address pinned for both connections.
        addresses.sort(key=lambda ip: ipaddress.ip_address(ip).version)
        writer = None
        started = time.monotonic()
        for ip in addresses[:3]:
            try:
                _, writer = await asyncio.wait_for(
                    tls_connect(ip, domain, ["h2"]), timeout=3.5
                )
                result["ip"] = ip
                break
            except (OSError, asyncio.TimeoutError):
                continue
        if writer is None:
            result["reason"] = "Не удалось проверить TLS 1.3 и сертификат"
            return
        try:
            tls = writer.get_extra_info("ssl_object")
            result.update(
                tls_version=tls.version(),
                alpn=tls.selected_alpn_protocol(),
                certificate_ok=True,
                latency_ms=round((time.monotonic() - started) * 1000),
            )
        finally:
            await close_connection(writer)
        if result["tls_version"] != "TLSv1.3" or result["alpn"] != "h2":
            result["reason"] = "Требуются TLS 1.3 и HTTP/2 (h2)"
            return
        # Separate HTTP/1.1 handshake to inspect redirects without an h2 dependency.
        reader, writer = await tls_connect(result["ip"], domain, ["http/1.1"])
        try:
            writer.write(
                f"GET / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\nUser-Agent: MD-Next-TLS-Check/1.0\r\n\r\n".encode(
                    "ascii"
                )
            )
            await writer.drain()
            status_line = await reader.readline()
            match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})[^\r\n]*\r?\n", status_line)
            if not match:
                result["reason"] = "Не удалось распознать HTTP-ответ"
                return
            status = int(match[1])
            result["http_status"] = status
            result["eligible"] = 200 <= status < 300
            result["reason"] = (
                "Прошёл базовую проверку"
                if result["eligible"]
                else (
                    "Сайт перенаправляет запрос; выберите конечный домен"
                    if 300 <= status < 400
                    else "Сайт не подтвердил обычный доступ (HTTP %s)" % status
                )
            )
        finally:
            await close_connection(writer)

    try:
        await asyncio.wait_for(run(), timeout=SCAN_TIMEOUT)
    except asyncio.TimeoutError:
        result["reason"] = "Время проверки истекло"
    except ValueError as exc:
        result["reason"] = str(exc)
    except ssl.SSLCertVerificationError:
        result["reason"] = "Не удалось подтвердить сертификат домена"
    except (OSError, ssl.SSLError):
        result["reason"] = "Ошибка DNS, соединения или TLS"
    return result


async def scan_domains(domains: list[str], excluded_domain: str = "") -> dict:
    domains = normalize_domains(domains)
    if _scan_lock.locked():
        raise RuntimeError("Сканирование уже выполняется. Повторите позже.")
    async with _scan_lock:
        semaphore = asyncio.Semaphore(3)

        async def check(domain):
            async with semaphore:
                if domain == excluded_domain.strip().lower().rstrip("."):
                    return dict(
                        domain=domain,
                        target=f"{domain}:443",
                        eligible=False,
                        reason="Домен панели нельзя использовать как внешний target",
                    )
                return await probe_domain(domain)

        results = await asyncio.gather(*(check(domain) for domain in domains))
    results.sort(
        key=lambda row: (
            not row["eligible"],
            (
                row.get("latency_ms")
                if row.get("latency_ms") is not None
                else float("inf")
            ),
        )
    )
    return {
        "results": results,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "vantage": "panel",
        "note": "Проверка с сервера панели, напрямую, без WARP и выходной ноды. Не проверяет доступность из сети клиента и не гарантирует незаметность.",
    }
