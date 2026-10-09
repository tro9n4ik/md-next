"""Per-client advertising domain filtering before user routing rules."""
import ipaddress
from sqlalchemy import select
from app.models.setting import Setting
from app.services.client_limits import access_allowed
from app.services.routing_rules import validate_rule_value
ADS_DOMAIN = "geosite:category-ads-all"

async def enabled_for_client(db, client_id: int) -> bool:
    row = await db.get(Setting, f"client.adblock.{client_id}")
    return bool(row and row.value == "true")

async def build_adblock_rules(db, profiles, kinds: set[str]) -> list[dict]:
    rows = (await db.execute(select(Setting).where(Setting.key.like("client.adblock.%")))).scalars().all()
    enabled = {int(row.key.rsplit(".", 1)[-1]) for row in rows
               if row.value == "true" and row.key.rsplit(".", 1)[-1].isdecimal()}
    users, sources = set(), set()
    for profile, client in profiles:
        if client.id not in enabled or not access_allowed(client):
            continue
        if profile.kind == "vless_xhttp_tls" and profile.uuid and profile.kind in kinds:
            users.add(f"c{client.id}-cdn@md-next")
        if not profile.is_enabled or profile.kind not in kinds:
            continue
        if profile.kind == "awg":
            if profile.ip_address:
                sources.add(str(ipaddress.ip_interface(profile.ip_address).ip))
        elif profile.kind.startswith("vless_") or profile.kind == "hysteria2":
            users.add(f"c{client.id}-{profile.kind}@md-next")
    if not users and not sources:
        return []
    validate_rule_value(ADS_DOMAIN)
    rules = []
    if users:
        rules.append({"type": "field", "domain": [ADS_DOMAIN], "user": sorted(users), "outboundTag": "block"})
    if sources:
        rules.append({"type": "field", "domain": [ADS_DOMAIN], "inboundTag": ["awg-in"],
                      "source": sorted(sources), "outboundTag": "block"})
    return rules
