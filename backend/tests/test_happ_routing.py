import base64
import json
import unittest
from urllib.parse import unquote

from app.services.happ_routing import build_happ_routing_link


def test_happ_routing_link_contains_configured_dns():
    link = build_happ_routing_link({
        "dns.remote_type": "DoH",
        "dns.remote_domain": "https://cloudflare-dns.com/dns-query",
        "dns.remote_ip": "1.1.1.1",
        "dns.domestic_type": "DoU",
        "dns.domestic_domain": "",
        "dns.domestic_ip": "8.8.8.8",
        "dns.domain_strategy": "IPIfNonMatch",
        "dns.fake_dns": "false",
    })

    prefix = "happ://routing/onadd/"
    assert link.startswith(prefix)
    payload = unquote(link.removeprefix(prefix))
    profile = json.loads(base64.b64decode(payload))
    assert profile["Name"] == "MD-Next DNS"
    assert profile["RemoteDNSType"] == "DoH"
    assert profile["RemoteDNSDomain"] == "https://cloudflare-dns.com/dns-query"
    assert profile["RemoteDNSIP"] == "1.1.1.1"
    assert profile["DomesticDNSType"] == "DoU"
    assert profile["DomainStrategy"] == "IPIfNonMatch"
    assert profile["FakeDNS"] == "false"


class HappRoutingProfileTest(unittest.TestCase):
    def test_profile_is_decodable(self):
        link = build_happ_routing_link({})
        prefix = "happ://routing/onadd/"
        profile = json.loads(base64.b64decode(unquote(link.removeprefix(prefix))))
        self.assertEqual(profile["RemoteDNSIP"], "1.1.1.1")
        self.assertEqual(profile["RemoteDNSType"], "DoH")


def test_happ_routes_russian_and_local_destinations_directly_without_disabling_vpn():
    link = build_happ_routing_link({})
    profile = json.loads(base64.b64decode(unquote(link.removeprefix("happ://routing/onadd/"))))
    assert profile["DirectSites"] == ["geosite:category-ru"]
    assert profile["DirectIp"] == [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
        "224.0.0.0/4", "255.255.255.255", "geoip:ru",
    ]
    assert profile["GlobalProxy"] == "true"
    assert profile["ProxySites"] == profile["ProxyIp"] == []
    assert profile["BlockSites"] == profile["BlockIp"] == []
    assert profile["Name"] == "MD-Next DNS"  # Обновляет прежний профиль вместо создания дубля.
    assert profile["Geoipurl"].startswith("https://github.com/v2fly/geoip/")
    assert profile["Geositeurl"].endswith("/dlc.dat")
