import ipaddress
import os
from pathlib import Path


def validate_rule_value(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Правило маршрутизации не может быть пустым")
    if value.startswith("geosite:"):
        if not value.removeprefix("geosite:"):
            raise ValueError("После geosite: укажите категорию, например geosite:google")
        _require_asset("geosite.dat")
        return "domain"
    if value.startswith("geoip:"):
        if not value.removeprefix("geoip:"):
            raise ValueError("После geoip: укажите категорию, например geoip:ru")
        _require_asset("geoip.dat")
        return "ip"
    if value.startswith("domain:") or value.startswith("full:"):
        if not value.split(":", 1)[1]:
            raise ValueError("Укажите домен после префикса domain: или full:")
        return "domain"
    try:
        ipaddress.ip_network(value, strict=False)
        return "ip"
    except ValueError:
        pass
    raise ValueError("Используйте domain:, full:, geosite:, geoip: или IP/CIDR")


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
