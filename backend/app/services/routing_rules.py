import os
from pathlib import Path
from app.services.input_validation import routing_match_type


def validate_rule_value(value: str) -> str:
    value = value.strip()
    match_type = routing_match_type(value)
    if value.startswith("geosite:"):
        _require_asset("geosite.dat")
        return "domain"
    if value.startswith("geoip:"):
        _require_asset("geoip.dat")
        return "ip"
    return match_type


def _require_asset(filename: str) -> None:
    configured = os.getenv("XRAY_LOCATION_ASSET")
    roots = [Path(configured)] if configured else []
    roots.extend((Path("/usr/local/share/xray"), Path("/usr/share/xray"), Path("/etc/xray"), Path("/usr/local/etc/xray")))
    if not any((root / filename).is_file() if root.is_dir() else root.name == filename and root.is_file() for root in roots):
        raise ValueError(f"Не найден {filename}. Установите файл в XRAY_LOCATION_ASSET или стандартный каталог Xray.")


def to_xray_rule(rule, nodes_by_id: dict[int, object]) -> dict:
    value = rule.domain_or_ip.strip()
    match_type = validate_rule_value(value)
    if rule.action == "proxy":
        node = nodes_by_id.get(rule.target_node_id)
        if node is None or not getattr(node, "is_enabled", True):
            raise ValueError(f"Для правила {rule.id} выбрана отсутствующая или выключенная нода")
        outbound_tag = f"node-{node.id}"
    elif rule.action == "direct":
        outbound_tag = "direct"
    elif rule.action == "block":
        outbound_tag = "block"
    elif rule.action == "warp":
        outbound_tag = "warp"
    else:
        raise ValueError(f"Недопустимое действие правила: {rule.action}")
    return {"type": "field", match_type: [value], "outboundTag": outbound_tag}
