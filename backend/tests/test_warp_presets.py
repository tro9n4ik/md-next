import pytest

from app.services import warp_presets
from app.models.routing import RoutingRule
from app.services.warp_presets import PRESETS, PRESET_ORDER, apply_preset, ensure_warp_rules_enabled, get_preset, is_preset_rule, preset_state, remove_preset, rule_value
from app.db.database import AsyncSessionLocal


async def _warp_rules(session) -> list:
    from sqlalchemy import select
    return list((await session.execute(select(RoutingRule))).scalars().all())


def test_every_preset_key_is_unique_and_ordered():
    assert len(PRESET_ORDER) == len(set(PRESET_ORDER))
    assert set(PRESET_ORDER) == set(PRESETS)


def test_presets_cover_requested_services():
    domains = [
        domain for preset in PRESETS.values() for domain in preset.domains
    ]
    for host in ("generativelanguage.googleapis.com", "openai.com", "anthropic.com", "perplexity.ai", "x.ai"):
        assert any(host == domain or host.endswith('.' + domain) for domain in domains)


def test_gemini_auth_api_and_resources_share_warp_domains():
    # Эти службы видны в действительных ответах Gemini/AI Studio и участвуют
    # в авторизации приложения. Основная страница одна не подтверждает доступ.
    domains = PRESETS["gemini"].domains
    for host in ("gemini.google.com", "aistudio.google.com", "accounts.google.com",
                 "myaccount.google.com", "www.google.com", "oauth2.googleapis.com",
                 "content.googleapis.com", "generativelanguage.googleapis.com",
                 "geminiweb-pa.clients6.google.com", "alkalimakersuite-pa.clients6.google.com",
                 "gemini.gstatic.com", "fonts.gstatic.com", "lh3.googleusercontent.com",
                 "gemini.app.google"):
        assert any(host == domain or host.endswith('.' + domain) for domain in domains), host
    assert not any("evilgoogle.com" == domain or "evilgoogle.com".endswith('.' + domain) for domain in domains)


def test_domains_are_deduplicated_within_a_preset():
    for preset in PRESETS.values():
        assert len(set(preset.domains)) == len(preset.domains)


def test_domains_are_lowercase_and_have_no_scheme():
    for preset in PRESETS.values():
        for domain in preset.domains:
            assert domain == domain.lower()
            assert not domain.startswith(("http://", "https://"))
            assert " " not in domain


def test_unknown_preset_raises_key_error():
    with pytest.raises(KeyError):
        get_preset("нет-такого")
    with pytest.raises(KeyError):
        get_preset("")


def test_rule_value_uses_domain_prefix():
    assert rule_value("openai.com") == "domain:openai.com"


def test_preset_rule_detection_requires_marker():
    assert is_preset_rule(type("R", (), {"description": "warp-preset:gemini (openai.com)"})()) is True
    assert is_preset_rule(type("R", (), {"description": "вручную"})()) is False
    assert is_preset_rule(type("R", (), {"description": None})()) is False


def test_preset_rule_detection_is_scoped_to_key():
    rule = type("R", (), {"description": "warp-preset:gemini (a.com)"})()
    assert is_preset_rule(rule, "gemini") is True
    assert is_preset_rule(rule, "claude") is False


@pytest.mark.asyncio
async def test_apply_creates_all_rules():
    async with AsyncSessionLocal() as session:
        result = await apply_preset(session, "gemini")
        await session.commit()

        assert len(result["created"]) == len(PRESETS["gemini"].domains)
        rules = [rule for rule in await _warp_rules(session) if is_preset_rule(rule, "gemini")]
        assert len(rules) == len(PRESETS["gemini"].domains)
        assert all(rule.action == "warp" for rule in rules)
        assert all(rule.is_active for rule in rules)


@pytest.mark.asyncio
async def test_apply_is_idempotent():
    async with AsyncSessionLocal() as session:
        await apply_preset(session, "claude")
        await session.commit()
        before = len(await _warp_rules(session))

        result = await apply_preset(session, "claude")
        await session.commit()

        assert result["created"] == []
        assert result["removed"] == []
        assert len(await _warp_rules(session)) == before


@pytest.mark.asyncio
async def test_apply_does_not_touch_manual_rules():
    async with AsyncSessionLocal() as session:
        session.add(RoutingRule(domain_or_ip="domain:example.org", action="block", description="вручную"))
        await session.commit()

        await apply_preset(session, "gemini")
        await session.commit()

        manual = [rule for rule in await _warp_rules(session) if rule.description == "вручную"]
        assert len(manual) == 1
        assert manual[0].action == "block"


@pytest.mark.asyncio
async def test_apply_keeps_other_presets():
    async with AsyncSessionLocal() as session:
        await apply_preset(session, "gemini")
        await session.commit()
        before = len([rule for rule in await _warp_rules(session) if is_preset_rule(rule, "gemini")])

        result = await apply_preset(session, "claude")
        await session.commit()

        assert result["kept_foreign"] == before
        assert len([rule for rule in await _warp_rules(session) if is_preset_rule(rule, "gemini")]) == before


@pytest.mark.asyncio
async def test_apply_reactivates_disabled_rule():
    async with AsyncSessionLocal() as session:
        await apply_preset(session, "gemini")
        await session.commit()
        rule = next(rule for rule in await _warp_rules(session) if is_preset_rule(rule, "gemini"))
        rule.is_active = False
        rule.action = "direct"
        await session.commit()

        result = await apply_preset(session, "gemini")
        await session.commit()

        assert len(result["updated"]) == 1
        assert result["created"] == []
        restored = next(rule for rule in await _warp_rules(session) if rule.id == rule.id and is_preset_rule(rule, "gemini"))
        assert restored.is_active is True
        assert restored.action == "warp"


@pytest.mark.asyncio
async def test_apply_removes_stale_domains_of_same_preset():
    async with AsyncSessionLocal() as session:
        session.add(RoutingRule(
            domain_or_ip="domain:old-service.example",
            action="warp",
            description="warp-preset:gemini (old-service.example)",
        ))
        await session.commit()

        result = await apply_preset(session, "gemini")
        await session.commit()

        assert "domain:old-service.example" in result["removed"]
        remaining = [rule.domain_or_ip for rule in await _warp_rules(session) if is_preset_rule(rule, "gemini")]
        assert "domain:old-service.example" not in remaining


@pytest.mark.asyncio
async def test_remove_deletes_only_target_preset():
    async with AsyncSessionLocal() as session:
        await apply_preset(session, "gemini")
        await apply_preset(session, "claude")
        await session.commit()

        result = await remove_preset(session, "gemini")
        await session.commit()

        assert len(result["removed"]) == len(PRESETS["gemini"].domains)
        assert not [rule for rule in await _warp_rules(session) if is_preset_rule(rule, "gemini")]
        assert [rule for rule in await _warp_rules(session) if is_preset_rule(rule, "claude")]


@pytest.mark.asyncio
async def test_remove_is_safe_when_nothing_applied():
    async with AsyncSessionLocal() as session:
        result = await remove_preset(session, "chatgpt")
        await session.commit()
        assert result["removed"] == []


@pytest.mark.asyncio
async def test_preset_state_reports_missing_and_extra():
    async with AsyncSessionLocal() as session:
        await remove_preset(session, "chatgpt")
        await session.commit()

        state = await preset_state(session)
        expected = len(PRESETS["chatgpt"].domains)
        assert state["chatgpt"]["total"] == 0
        assert state["chatgpt"]["missing"] == expected
        assert state["chatgpt"]["extra"] == 0


@pytest.mark.asyncio
async def test_ensure_warp_rules_enabled_turns_off_on():
    from app.models.setting import Setting
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        setting = (await session.execute(
            select(Setting).where(Setting.key == "warp.usage")
        )).scalar_one_or_none()
        if setting is None:
            session.add(Setting(key="warp.usage", value="off"))
        else:
            setting.value = "off"
        await session.commit()

        changed = await ensure_warp_rules_enabled(session)
        await session.commit()

        assert changed is True
        updated = (await session.execute(
            select(Setting).where(Setting.key == "warp.usage")
        )).scalar_one()
        assert updated.value == "rules"


@pytest.mark.asyncio
async def test_ensure_warp_rules_enabled_is_noop_when_already_rules():
    from app.models.setting import Setting
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        setting = (await session.execute(
            select(Setting).where(Setting.key == "warp.usage")
        )).scalar_one_or_none()
        if setting is None:
            session.add(Setting(key="warp.usage", value="rules"))
        else:
            setting.value = "rules"
        await session.commit()

        changed = await ensure_warp_rules_enabled(session)
        await session.commit()

        assert changed is False
        updated = (await session.execute(
            select(Setting).where(Setting.key == "warp.usage")
        )).scalar_one()
        assert updated.value == "rules"
