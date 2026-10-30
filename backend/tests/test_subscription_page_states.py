"""Публичная страница предлагает только предоставленные и доступные подключения."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

from app.models.client import Client, ClientProfile
from app.services.crypto import encrypt_secret
from app.services.subscription_page import render_subscription_page


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href")


def client(**changes):
    now = datetime.now(timezone.utc)
    values = dict(id=1, name="Владелец", phone="", email="", is_active=True,
                  created_at=now, traffic_period_start=now, expires_at=now+timedelta(days=5),
                  monthly_traffic_limit=1000, monthly_traffic_up=0, monthly_traffic_down=0)
    return Client(**{**values, **changes})


def render(owner, profiles, enabled, **settings):
    return render_subscription_page(owner, profiles,
        {"protocol.reality.server_address": "vpn.example.com", "mtproto.domain": "example.com", **settings},
        enabled, "https://panel.example.com/sub/test?format=raw", 0, 1000)


def test_only_provided_profile_is_recommended_and_imported():
    profiles = [ClientProfile(kind="vless_reality_tcp", is_enabled=False),
                ClientProfile(kind="vless_xhttp_tls", is_enabled=True, uuid="private-profile-id")]
    html = render(client(), profiles, {"vless_reality_tcp", "vless_xhttp_tls"},
                  **{"subscription.name": '<b>Моя сеть</b>'})
    assert "Начните с <strong>XHTTP TLS</strong>" in html
    assert "Начните с <strong>Reality TCP</strong>" not in html
    assert "private-profile-id" not in html
    assert "&lt;b&gt;Моя сеть&lt;/b&gt;" in html and "<b>Моя сеть</b>" not in html
    hrefs = Links(html).hrefs
    assert any(link.startswith("happ://add/") for link in hrefs)
    # Пустой расход при установленном лимите всё равно имеет индикатор.
    assert 'aria-label="Использованный трафик"' in html and 'aria-valuenow="0"' in html


@pytest.mark.parametrize("state", ["disabled", "expired", "monthly_quota"])
def test_blocked_subscriptions_do_not_offer_live_imports(state):
    changes = {"is_active": False} if state == "disabled" else (
        {"expires_at": datetime.now(timezone.utc)-timedelta(days=1)} if state == "expired" else
        {"monthly_traffic_up": 1000})
    profiles = [ClientProfile(kind="vless_reality_tcp", is_enabled=True),
                ClientProfile(kind="mtproto", is_enabled=True, private_key_enc=encrypt_secret("a"*32))]
    html = render(client(**changes), profiles, {"vless_reality_tcp", "mtproto"})
    assert not any(link.startswith(("happ://", "v2rayng://", "tg://")) for link in Links(html).hrefs)
    assert 'id="platform"' not in html
    assert "После возобновления доступа" in html


def test_cdn_only_exhausted_has_no_empty_vpn_import_but_keeps_telegram():
    owner = client(cdn_monthly_traffic_limit=100, cdn_monthly_traffic_down=100)
    profiles = [ClientProfile(kind="vless_xhttp_tls", is_enabled=False, uuid="private-cdn-id"),
                ClientProfile(kind="mtproto", is_enabled=True, private_key_enc=encrypt_secret("a"*32))]
    html = render(owner, profiles, {"vless_xhttp_tls", "mtproto"},
                  **{"cdn.enabled": "true", "cdn.domain": "cdn.example.com", "client.cdn.1": "true"})
    hrefs = Links(html).hrefs
    assert any(link.startswith("tg://proxy?") for link in hrefs)
    assert not any(link.startswith(("happ://", "v2rayng://")) for link in hrefs)
    assert "Лимит обхода БС исчерпан" in html


def test_available_cdn_is_recommended_when_regular_tls_is_not_granted():
    profiles = [ClientProfile(kind="vless_xhttp_tls", is_enabled=False, uuid="private-cdn-id")]
    html = render(client(), profiles, {"vless_xhttp_tls"},
                  **{"cdn.enabled": "true", "cdn.domain": "cdn.example.com", "client.cdn.1": "true"})
    assert "Начните с <strong>Обход БС</strong>" in html
    assert "Начните с <strong>XHTTP TLS</strong>" not in html
    assert "private-cdn-id" not in html


def test_awg_only_explains_separate_configuration_without_vpn_subscription_import():
    html = render(client(), [ClientProfile(kind="awg", is_enabled=True)], {"awg"})
    assert "Подключение AmneziaWG" in html and "файл конфигурации .conf" in html
    assert not any(link.startswith(("happ://", "v2rayng://")) for link in Links(html).hrefs)
