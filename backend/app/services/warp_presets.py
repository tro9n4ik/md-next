"""Готовые пресеты маршрутизации через WARP.

Смысл в том, чтобы после установки панели не приходилось разбираться с geosite-файлами
и вручную набирать домены. Администратор выбирает пресет, а панель сама создаёт нужные
правила, включает режим «по правилам» и применяет конфигурацию Xray.

Домены перечислены явно, а не через geosite:, потому что geosite требует наличия
geosite.dat на сервере. Явный список работает на любой установке и не зависит от того,
какая версия geo-ассетов скачалась.
"""

from typing import Dict, List, NamedTuple, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.routing import RoutingRule
from app.models.setting import Setting


class WarpPreset(NamedTuple):
    key: str
    title: str
    description: str
    domains: List[str]


_PRESETS: List[WarpPreset] = [
    WarpPreset(
        key="gemini",
        title="Gemini и Google AI Studio",
        description=(
            "Gemini, Google AI Studio и службы Google используют единый выход WARP, "
            "включая авторизацию, API и загрузку ресурсов. Пресет также направляет "
            "через WARP другие сервисы на этих доменах Google. Доступность зависит от "
            "того, как Google определяет регион выхода и условия аккаунта."
        ),
        domains=[
            "google.com",
            "googleapis.com",
            "googleusercontent.com",
            "gstatic.com",
            "gemini.app.google",
        ],
    ),
    WarpPreset(
        key="chatgpt",
        title="ChatGPT и OpenAI",
        description=(
            "Веб-интерфейс ChatGPT, API OpenAI и файлы ответов. "
            "Адреса oaistatic и oaiusercontent нужны для загрузки аватаров и артефактов."
        ),
        domains=[
            "openai.com",
            "chatgpt.com",
            "oaistatic.com",
            "oaiusercontent.com",
            "oai.com",
            "sora.com",
        ],
    ),
    WarpPreset(
        key="claude",
        title="Claude (Anthropic)",
        description="Веб-интерфейс Claude и API Anthropic.",
        domains=[
            "anthropic.com",
            "claude.ai",
            "claudeusercontent.com",
        ],
    ),
    WarpPreset(
        key="ai-services",
        title="Perplexity, Copilot и Grok",
        description=(
            "Perplexity AI, GitHub Copilot и Grok из xAI одним набором правил. "
            "Подходит, когда отдельные пресеты не нужны."
        ),
        domains=[
            "perplexity.ai",
            "copilot.microsoft.com",
            "githubcopilot.com",
            "api.githubcopilot.com",
            "copilot-proxy.githubusercontent.com",
            "x.ai",
            "grok.com",
            "assets.grok.com",
        ],
    ),
]

PRESETS: Dict[str, WarpPreset] = {preset.key: preset for preset in _PRESETS}
PRESET_ORDER: List[str] = [preset.key for preset in _PRESETS]

# Правила пресета помечаются этим префиксом в описании, чтобы их можно было
# отличить от созданных вручную и обновить при повторном применении.
PRESET_MARKER = "warp-preset:"


def get_preset(key: str) -> WarpPreset:
    preset = PRESETS.get((key or "").strip().lower())
    if preset is None:
        raise KeyError(key)
    return preset


def list_presets() -> List[WarpPreset]:
    return [_PRESETS[PRESET_ORDER.index(key)] for key in PRESET_ORDER]


def rule_value(domain: str) -> str:
    return f"domain:{domain.strip()}"


def preset_rule_description(preset: WarpPreset, domain: str) -> str:
    return f"{PRESET_MARKER}{preset.key} ({domain})"


def is_preset_rule(rule: RoutingRule, preset_key: Optional[str] = None) -> bool:
    description = (rule.description or "").strip()
    if not description.startswith(PRESET_MARKER):
        return False
    if preset_key is None:
        return True
    return description[len(PRESET_MARKER):].split(" ", 1)[0] == preset_key


async def preset_state(db: AsyncSession) -> Dict[str, Dict[str, int]]:
    """Сколько правил каждого пресета уже создано в базе."""
    rows = (await db.execute(select(RoutingRule))).scalars().all()
    state: Dict[str, Dict[str, int]] = {
        key: {"total": 0, "missing": 0, "extra": 0} for key in PRESET_ORDER
    }
    for rule in rows:
        for key in PRESET_ORDER:
            if is_preset_rule(rule, key):
                state[key]["total"] += 1
    for key, preset in PRESETS.items():
        expected = {rule_value(domain) for domain in preset.domains}
        present = {
            rule.domain_or_ip
            for rule in rows
            if is_preset_rule(rule, key)
        }
        state[key]["missing"] = len(expected - present)
        state[key]["extra"] = len(present - expected)
    return state


async def apply_preset(db: AsyncSession, key: str) -> Dict[str, object]:
    """Создаёт недостающие правила пресета и удаляет устаревшие.

    Повторное применение безопасно: уже существующие правила не дублируются,
    а правила ручной настройки не трогаются.
    """
    preset = get_preset(key)
    rules = (await db.execute(select(RoutingRule))).scalars().all()

    existing: Dict[str, RoutingRule] = {}
    foreign: List[RoutingRule] = []
    for rule in rules:
        if is_preset_rule(rule, preset.key):
            existing[rule.domain_or_ip.strip()] = rule
        elif is_preset_rule(rule):
            foreign.append(rule)

    created: List[str] = []
    updated: List[str] = []
    removed: List[str] = []

    expected = {rule_value(domain): domain for domain in preset.domains}

    for value, domain in expected.items():
        current = existing.pop(value, None)
        if current is None:
            db.add(RoutingRule(
                domain_or_ip=value,
                action="warp",
                description=preset_rule_description(preset, domain),
                is_active=True,
            ))
            created.append(domain)
        elif current.action != "warp" or not current.is_active:
            current.action = "warp"
            current.is_active = True
            current.description = preset_rule_description(preset, domain)
            updated.append(domain)

    for value, rule in existing.items():
        await db.delete(rule)
        removed.append(value)

    await db.flush()
    return {
        "preset": preset.key,
        "title": preset.title,
        "created": created,
        "updated": updated,
        "removed": removed,
        # Правила других пресетов намеренно остаются нетронутыми.
        "kept_foreign": len(foreign),
    }


async def remove_preset(db: AsyncSession, key: str) -> Dict[str, object]:
    preset = get_preset(key)
    rules = (await db.execute(select(RoutingRule))).scalars().all()
    removed: List[str] = []
    for rule in rules:
        if is_preset_rule(rule, preset.key):
            removed.append(rule.domain_or_ip.strip())
            await db.delete(rule)
    await db.flush()
    return {"preset": preset.key, "title": preset.title, "removed": removed}


async def ensure_warp_rules_enabled(db: AsyncSession) -> bool:
    """Включает режим «WARP по правилам», если WARP был выключен.

    Без этого правила пресета ссылались бы на outbound, которого в конфиге Xray нет:
    xray.py создаёт outbound warp только когда warp.usage != off.
    """
    setting = (await db.execute(
        select(Setting).where(Setting.key == "warp.usage")
    )).scalar_one_or_none()
    if setting is None:
        db.add(Setting(key="warp.usage", value="rules"))
        await db.flush()
        return True
    if setting.value == "off":
        setting.value = "rules"
        await db.flush()
        return True
    return False


async def list_preset_rules(db: AsyncSession, key: str) -> List[str]:
    preset = get_preset(key)
    rules = (await db.execute(
        select(RoutingRule).where(
            or_(
                RoutingRule.description.like(f"{PRESET_MARKER}{preset.key}%"),
                RoutingRule.domain_or_ip.in_([rule_value(domain) for domain in preset.domains]),
            )
        )
    )).scalars().all()
    return [rule.domain_or_ip.strip() for rule in rules]
