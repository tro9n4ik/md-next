"""Защита клиентских ссылок от неожиданной смены.

Клиентская ссылка не должна меняться при замене ноды: нода — расходный материал,
а адрес в ссылке, Reality-ключи, SNI и short_id задают идентичность панели для клиента.
Ниже собрана логика, которая отделяет безопасные правки настроек от тех, что
необратимо ломают подписки всех клиентов сразу.
"""

import ipaddress
from typing import Any, Dict, Iterable, Optional, Tuple

LINK_IDENTITY_FIELDS: Tuple[str, ...] = (
    "server_address",
    "server_name",
    "fingerprint",
    "short_id",
    "public_key",
    "flow",
)

FIELD_LABELS: Dict[str, str] = {
    "server_address": "публичный адрес",
    "server_name": "Reality SNI",
    "fingerprint": "fingerprint",
    "short_id": "short ID",
    "public_key": "Reality public key",
    "flow": "XTLS Flow",
}


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def resolve_identity_fields(current: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, str]:
    """Оставляет только те поля идентичности, которые реально изменились.

    Пустые значения игнорируются: частичное обновление не должно стирать
    уже сохранённые параметры и превращать их в «изменение».
    """
    changed: Dict[str, str] = {}
    for field in LINK_IDENTITY_FIELDS:
        new_value = _normalize(incoming.get(field))
        if not new_value:
            continue
        if _normalize(current.get(field)) != new_value:
            changed[field] = new_value
    return changed


def describe_identity_change(changed: Dict[str, str]) -> str:
    if not changed:
        return ""
    labels = ", ".join(FIELD_LABELS.get(field, field) for field in changed)
    return (
        f"Изменение затронувает {labels}. Все клиентские ссылки и подписки "
        "перестроятся, ранее выданные ссылки перестанут работать."
    )


def is_identity_change_blocked(changed: Dict[str, str], confirmed: bool) -> bool:
    """Требует явного подтверждения смены идентичности панели."""
    return bool(changed) and not confirmed


def node_hosts(nodes: Iterable[Any]) -> set[str]:
    """Нормализованные адреса нод, чтобы отсечь подмену адреса панели на адрес ноды."""
    hosts = set()
    for node in nodes:
        host = _normalize(getattr(node, "host", "")).lower()
        if host:
            hosts.add(host)
    return hosts


def find_node_host_conflict(values: Dict[str, Any], node_hosts_set: set[str]) -> Optional[str]:
    """Возвращает имя поля, в которое попал адрес ноды вместо адреса панели.

    Проверяются только IP-адреса: ноды регистрируются по публичному IP, который
    определяет join-node.sh. Домен панели может совпадать с адресом ноды, если нода
    поднята на том же сервере, и такой случай не считается ошибкой.
    """
    for field in ("server_address", "server_name"):
        value = _normalize(values.get(field)).lower()
        if value and value in node_hosts_set and _is_ip_literal(value):
            return field
    return None


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True