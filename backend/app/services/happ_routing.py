import base64
import json
import time
from urllib.parse import quote, urlparse
import ipaddress


def build_happ_routing_link(settings: dict[str, str], *, adblock_enabled: bool = False) -> str:
    """DNS и прямой доступ к локальным сетям и российским ресурсам в Happ."""
    hosts = {}
    for kind in ("remote", "domestic"):
        dns_type = settings.get(f"dns.{kind}_type", "DoH" if kind == "remote" else "DoU")
        domain = settings.get(f"dns.{kind}_domain", "https://cloudflare-dns.com/dns-query" if kind == "remote" else "")
        address = settings.get(f"dns.{kind}_ip", "1.1.1.1" if kind == "remote" else "8.8.8.8")
        if dns_type != "DoH":
            continue
        try:
            hostname = urlparse(domain).hostname
            ipaddress.ip_address(address)
            if hostname:
                # Начальный запрос DoH не должен разрешать имя своего сервера через сам DoH.
                hosts.setdefault(hostname, address)
        except ValueError:
            continue
    profile = {
        "Name": "MD-Next DNS",
        "GlobalProxy": "true",
        # Реклама должна проверяться до category-ru и geoip:ru (прямой выход).
        "RouteOrder": "block-proxy-direct",
        "RemoteDNSType": settings.get("dns.remote_type", "DoH"),
        "RemoteDNSDomain": settings.get("dns.remote_domain", "https://cloudflare-dns.com/dns-query"),
        "RemoteDNSIP": settings.get("dns.remote_ip", "1.1.1.1"),
        "DomesticDNSType": settings.get("dns.domestic_type", "DoU"),
        "DomesticDNSDomain": settings.get("dns.domestic_domain", ""),
        "DomesticDNSIP": settings.get("dns.domestic_ip", "8.8.8.8"),
        "Geoipurl": "https://github.com/v2fly/geoip/releases/latest/download/geoip.dat",
        "Geositeurl": "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat",
        "LastUpdated": str(int(time.time())),
        "DnsHosts": hosts,
        "DirectSites": ["geosite:category-ru"],
        "DirectIp": [
            "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
            "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255", "geoip:ru",
        ],
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": ["geosite:category-ads-all"] if adblock_enabled else [],
        "BlockIp": [],
        "DomainStrategy": settings.get("dns.domain_strategy", "IPIfNonMatch"),
        "FakeDNS": str(settings.get("dns.fake_dns", "false")).lower(),
    }
    encoded = base64.b64encode(json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return f"happ://routing/onadd/{quote(encoded, safe='')}"
