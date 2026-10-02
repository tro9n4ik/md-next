import base64
import json
import time
from urllib.parse import quote


def build_happ_routing_link(settings: dict[str, str]) -> str:
    """Формирует ссылку Happ с DNS-настройками профиля MD-Next."""
    profile = {
        "Name": "MD-Next DNS",
        "GlobalProxy": "true",
        "RemoteDNSType": settings.get("dns.remote_type", "DoH"),
        "RemoteDNSDomain": settings.get("dns.remote_domain", "https://cloudflare-dns.com/dns-query"),
        "RemoteDNSIP": settings.get("dns.remote_ip", "1.1.1.1"),
        "DomesticDNSType": settings.get("dns.domestic_type", "DoU"),
        "DomesticDNSDomain": settings.get("dns.domestic_domain", ""),
        "DomesticDNSIP": settings.get("dns.domestic_ip", "8.8.8.8"),
        "Geoipurl": "",
        "Geositeurl": "",
        "LastUpdated": str(int(time.time())),
        "DnsHosts": {},
        "DirectSites": [],
        "DirectIp": [],
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": [],
        "BlockIp": [],
        "DomainStrategy": settings.get("dns.domain_strategy", "IPIfNonMatch"),
        "FakeDNS": str(settings.get("dns.fake_dns", "false")).lower(),
    }
    encoded = base64.b64encode(json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return f"happ://routing/onadd/{quote(encoded, safe='')}"
