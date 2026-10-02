from types import SimpleNamespace

from app.services.links_guard import (
    LINK_IDENTITY_FIELDS,
    describe_identity_change,
    find_node_host_conflict,
    is_identity_change_blocked,
    node_hosts,
    resolve_identity_fields,
)


CURRENT = {
    "server_address": "panel.example.com",
    "server_name": "panel.example.com",
    "fingerprint": "chrome",
    "short_id": "a1b2c3d4",
    "public_key": "PublicKeyOld",
    "flow": "xtls-rprx-vision",
}


def test_unchanged_reality_block_is_not_an_identity_change():
    assert resolve_identity_fields(CURRENT, dict(CURRENT)) == {}


def test_partial_payload_does_not_wipe_untouched_fields():
    incoming = {field: CURRENT[field] for field in ("server_address", "server_name", "fingerprint", "short_id", "public_key", "flow")}
    incoming["target"] = "127.0.0.1:8080"
    assert resolve_identity_fields(CURRENT, incoming) == {}


def test_changed_public_key_is_detected():
    changed = resolve_identity_fields(CURRENT, {**CURRENT, "public_key": "PublicKeyNew"})
    assert changed == {"public_key": "PublicKeyNew"}
    assert "открытый ключ Reality" in describe_identity_change(changed)


def test_empty_incoming_value_is_ignored():
    assert resolve_identity_fields(CURRENT, {"short_id": "", "public_key": ""}) == {}


def test_whitespace_is_normalized_before_comparison():
    assert resolve_identity_fields(CURRENT, {"fingerprint": " chrome "}) == {}


def test_all_identity_fields_are_covered():
    assert set(LINK_IDENTITY_FIELDS) == {
        "server_address",
        "server_name",
        "fingerprint",
        "short_id",
        "public_key",
        "flow",
    }


def test_blocking_requires_explicit_confirmation():
    changed = resolve_identity_fields(CURRENT, {"short_id": "ffffffff"})
    assert is_identity_change_blocked(changed, confirmed=False) is True
    assert is_identity_change_blocked(changed, confirmed=True) is False
    assert is_identity_change_blocked({}, confirmed=False) is False


def test_description_mentions_client_impact():
    changed = resolve_identity_fields(CURRENT, {"server_name": "other.example.com"})
    text = describe_identity_change(changed)
    assert "Reality SNI" in text
    assert "ссылк" in text


def test_node_ip_in_link_field_is_rejected():
    hosts = node_hosts([SimpleNamespace(host="203.0.113.10")])
    assert find_node_host_conflict({"server_address": "203.0.113.10"}, hosts) == "server_address"
    assert find_node_host_conflict({"server_name": "203.0.113.10"}, hosts) == "server_name"
    assert find_node_host_conflict({"server_address": "panel.example.com"}, hosts) is None
    assert find_node_host_conflict({}, hosts) is None


def test_node_hosted_on_same_server_as_panel_is_allowed():
    """Нода на том же сервере, что и панель, не должна блокировать настройку."""
    hosts = node_hosts([SimpleNamespace(host="panel.example.com")])
    assert find_node_host_conflict({"server_address": "panel.example.com"}, hosts) is None


def test_node_hosts_normalizes_case_and_blanks():
    hosts = node_hosts([SimpleNamespace(host="  Example.COM "), SimpleNamespace(host="")])
    assert hosts == {"example.com"}
