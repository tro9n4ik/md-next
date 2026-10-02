"""Проверка сквозного пути пресетов: Xray-конфиг должен ссылаться на существующий outbound warp.

Пресет создаёт правила с action=warp. Если warp.usage остался "off", xray.py не создаёт
outbound warp, и правила ссылаются в пустоту. Поэтому проверяем именно пару
"пресет применён + outbound warp присутствует + правило последним не перебито".
"""

import json
import sys

from app.services import warp_presets
from app.services.xray import XrayService

sys.path.insert(0, ".")


def build_config(nodes, active_node=None, warp_usage="rules", routing_rules=None, warp_port=40000):
    return json.loads(XrayService.generate_config(
        clients=[],
        server_private_key="private",
        server_name="example.com",
        active_node=active_node,
        profile_options={
            "enabled": {"vless_reality_tcp"},
            "warp_usage": warp_usage,
            "warp_port": warp_port,
            "routing_rules": routing_rules or [],
            "nodes": nodes,
        },
    ))


def rule_for(domain, action="warp", rule_id=1, node_id=None):
    payload = {"type": "field", "domain": [f"domain:{domain}"], "outboundTag": action}
    if node_id is not None:
        payload = {"type": "field", "domain": [f"domain:{domain}"], "outboundTag": f"node-{node_id}"}
    return payload


def test_warp_outbound_exists_when_usage_is_rules():
    config = build_config([], warp_usage="rules")
    tags = [item["tag"] for item in config["outbounds"]]
    assert "warp" in tags
    warp = next(item for item in config["outbounds"] if item["tag"] == "warp")
    assert warp["protocol"] == "socks"
    assert warp["settings"]["servers"][0]["address"] == "127.0.0.1"
    assert warp["settings"]["servers"][0]["port"] == 40000


def test_warp_outbound_absent_when_usage_off():
    config = build_config([], warp_usage="off")
    assert "warp" not in [item["tag"] for item in config["outbounds"]]


def test_every_preset_domain_produces_a_warp_rule():
    rules = [
        {"type": "field", "domain": [warp_presets.rule_value(domain)], "outboundTag": "warp"}
        for preset in warp_presets.list_presets()
        for domain in preset.domains
    ]
    config = build_config([], warp_usage="rules", routing_rules=rules)

    tags = [item["tag"] for item in config["outbounds"]]
    assert "warp" in tags, "outbound warp должен существовать для пресетов"

    routing_domains = set()
    for rule in config["routing"]["rules"]:
        for value in rule.get("domain", []):
            routing_domains.add(value)

    for preset in warp_presets.list_presets():
        for domain in preset.domains:
            assert warp_presets.rule_value(domain) in routing_domains, f"{domain} потерян в конфиге"


def test_all_presets_together_do_not_break_routing():
    rules = [
        {"type": "field", "domain": [warp_presets.rule_value(domain)], "outboundTag": "warp"}
        for preset in warp_presets.list_presets()
        for domain in preset.domains
    ]
    config = build_config([], warp_usage="rules", routing_rules=rules)

    # "api" не входит в список outbounds: он задаётся через секцию config["api"].
    outbounds = {item["tag"] for item in config["outbounds"]} | {"api"}
    for rule in config["routing"]["rules"]:
        assert rule["outboundTag"] in outbounds, f"правило ссылается на отсутствующий outbound {rule['outboundTag']}"


def test_preset_rules_survive_alongside_node_rules():
    node = {"id": 1, "host": "203.0.113.5", "port": 443, "protocol": "trojan", "secret": "s3cret"}
    rules = [
        {"type": "field", "domain": ["geosite:category-ads"], "outboundTag": "block"},
        {"type": "field", "domain": [warp_presets.rule_value("openai.com")], "outboundTag": "warp"},
        {"type": "field", "domain": [warp_presets.rule_value("claude.ai")], "outboundTag": "warp"},
    ]
    config = build_config([node], warp_usage="rules", routing_rules=rules)

    outbounds = {item["tag"] for item in config["outbounds"]}
    assert {"warp", "block", "direct", "node-1"} <= outbounds

    tags = [rule["outboundTag"] for rule in config["routing"]["rules"]]
    assert tags[:3] == ["api", "block", "warp"]