import json

import pytest

from app.services.xray import (
    NODE_BALANCER_TAG,
    PROBE_INBOUND_PREFIX,
    XrayService,
    probe_port_for_node,
)


class FakeNode:
    def __init__(self, id, host="203.0.113.10", port=443, protocol="trojan", secret="s3cret", is_enabled=True):
        self.id = id
        self.host = host
        self.port = port
        self.protocol = protocol
        self.secret = secret
        self.is_enabled = is_enabled


def build(active_node=None, **options):
    payload = {
        "enabled": {"vless_reality_tcp"},
        "nodes": [asdict(node) for node in (options.pop("nodes", None) or [])],
    }
    payload.update(options)
    return json.loads(
        XrayService.generate_config(
            clients=[],
            server_private_key="private",
            server_name="example.com",
            active_node=active_node,
            profile_options=payload,
        )
    )


def asdict(node):
    return {"id": node.id, "host": node.host, "port": node.port, "protocol": node.protocol, "secret": node.secret}


def test_without_active_node_no_balancer_and_direct_default():
    config = build(active_node=None)
    assert "balancers" not in config["routing"]
    assert "burstObservatory" not in config
    assert config["outbounds"][0]["tag"] == "direct"


def test_active_node_routes_through_balancer_with_direct_fallback():
    node = FakeNode(id=7)
    config = build(active_node=node)

    balancer = config["routing"]["balancers"][0]
    assert balancer["tag"] == NODE_BALANCER_TAG
    assert balancer["selector"] == ["node-7"]
    assert balancer["fallbackTag"] == "direct"
    assert config["routing"]["rules"][-1] == {
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": NODE_BALANCER_TAG,
    }
    assert config["burstObservatory"]["subjectSelector"] == ["node-"]


def test_fallback_tag_block_prevents_leaking_master_ip():
    node = FakeNode(id=3)
    config = build(active_node=node, node_fallback_tag="block")
    assert config["routing"]["balancers"][0]["fallbackTag"] == "block"


def test_unknown_fallback_tag_falls_back_to_direct():
    node = FakeNode(id=3)
    config = build(active_node=node, node_fallback_tag="что-то")
    assert config["routing"]["balancers"][0]["fallbackTag"] == "direct"


def test_active_node_without_secret_gets_no_balancer():
    config = build(active_node=FakeNode(id=5, secret=None))
    assert "balancers" not in config["routing"]
    assert not any(item["tag"].startswith("node-") for item in config["outbounds"])


def test_warp_all_disables_balancer():
    """WARP на весь трафик остаётся единственным маршрутом: балансер не добавляется."""
    node = FakeNode(id=7)
    config = build(active_node=node, warp_usage="all")
    assert "balancers" not in config["routing"]
    assert config["outbounds"][0]["tag"] == "node-7"
    assert config["routing"]["rules"][-1] != {
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": NODE_BALANCER_TAG,
    }


def test_user_routing_rules_run_before_catch_all():
    node = FakeNode(id=7)
    config = build(
        active_node=node,
        routing_rules=[{"type": "field", "domain": ["geosite:category-ads"], "outboundTag": "block"}],
    )
    rules = config["routing"]["rules"]
    assert rules[1]["outboundTag"] == "block"
    assert rules[-1]["outboundTag"] == NODE_BALANCER_TAG
    assert config["routing"]["domainStrategy"] == "AsIs"


def test_existing_warp_rule_stays_last_when_no_balancer(monkeypatch):
    """Поведение без активной ноды не меняется: правила пользователя остаются в конце."""
    config = json.loads(
        XrayService.generate_config(
            clients=[],
            server_private_key="private",
            server_name="example.com",
            profile_options={
                "enabled": {"vless_reality_tcp"},
                "warp_usage": "rules",
                "warp_port": 40123,
                "routing_rules": [{"type": "field", "domain": ["domain:example.org"], "outboundTag": "warp"}],
            },
        )
    )
    assert config["routing"]["rules"][-1]["outboundTag"] == "warp"


def test_probe_disabled_by_default():
    config = build(active_node=FakeNode(id=4))
    assert not any(item.get("tag", "").startswith(PROBE_INBOUND_PREFIX) for item in config["inbounds"])


def test_probe_inbound_is_pinned_to_its_node(monkeypatch):
    monkeypatch.setenv("NODE_PROBE_ENABLED", "true")
    monkeypatch.setenv("NODE_PROBE_BASE_PORT", "10900")
    config = build(active_node=FakeNode(id=4), nodes=[FakeNode(id=9)])

    inbounds = {item["tag"]: item for item in config["inbounds"] if item.get("tag", "").startswith(PROBE_INBOUND_PREFIX)}
    assert inbounds["probe-4"]["port"] == probe_port_for_node(4)
    assert inbounds["probe-9"]["port"] == probe_port_for_node(9)
    assert all(item["listen"] == "127.0.0.1" and item["protocol"] == "socks" for item in inbounds.values())

    probe_rules = {
        rule["inboundTag"][0]: rule["outboundTag"]
        for rule in config["routing"]["rules"]
        if rule.get("inboundTag", [""])[0].startswith(PROBE_INBOUND_PREFIX)
    }
    assert probe_rules == {"probe-4": "node-4", "probe-9": "node-9"}


def test_probe_rules_precede_user_rules(monkeypatch):
    monkeypatch.setenv("NODE_PROBE_ENABLED", "true")
    config = build(
        active_node=FakeNode(id=4),
        routing_rules=[{"type": "field", "domain": ["domain:example.org"], "outboundTag": "direct"}],
    )
    tags = [rule.get("inboundTag", [None])[0] for rule in config["routing"]["rules"]]
    assert tags.index("probe-4") < tags.index(None)


def test_probe_port_helpers(monkeypatch):
    monkeypatch.setenv("NODE_PROBE_BASE_PORT", "12000")
    assert probe_port_for_node(1) == 12001
    monkeypatch.setenv("NODE_PROBE_BASE_PORT", "не-число")
    assert probe_port_for_node(2) == 10902