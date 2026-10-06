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
