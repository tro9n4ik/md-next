from ipaddress import ip_address

from fastapi import Request


def request_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    # Штатный Nginx подключается по loopback и заменяет X-Real-IP.
    if peer == "127.0.0.1":
        forwarded = request.headers.get("X-Real-IP")
        if forwarded:
            try:
                return str(ip_address(forwarded))
            except ValueError:
                pass
    return peer
