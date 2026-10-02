import base64

import pytest
from sqlalchemy import select

from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.profiles import get_profile_settings, make_profile_data
from app.services.reality_keys import (
    derive_public_key,
    ensure_reality_key_pair,
    resolve_key_pair,
    resolve_public_key,
)
from conftest import TestingSessionLocal


def make_key_pair():
    """Настоящая пара X25519 в формате Xray."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    private = x25519.X25519PrivateKey.generate()
    priv_b64 = base64.urlsafe_b64encode(
        private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode().rstrip("=")
    pub_b64 = base64.urlsafe_b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode().rstrip("=")
    return priv_b64, pub_b64


def foreign_public_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    return base64.urlsafe_b64encode(
        x25519.X25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode().rstrip("=")


PRIVATE_KEY, PUBLIC_KEY = make_key_pair()


def test_derive_public_key_returns_matching_key():
    assert derive_public_key(PRIVATE_KEY) == PUBLIC_KEY


def test_derive_accepts_base64url_with_padding():
    padded = PRIVATE_KEY + "=="
    assert derive_public_key(padded) == PUBLIC_KEY


def test_foreign_public_key_really_is_foreign():
    # Тест бессмыслен, если «чужой» ключ вдруг совпал бы с настоящим.
    assert foreign_public_key() != PUBLIC_KEY


def test_resolve_public_key_repairs_mismatched_pair():
    # Ровно то состояние, из-за которого Reality отдавал клиентам настоящий
    # сертификат: приватный в конфиге есть, публичный в ссылках — чужой.
    assert resolve_public_key(PRIVATE_KEY, foreign_public_key()) == PUBLIC_KEY


def test_resolve_public_key_keeps_existing_value_when_private_absent():
    assert resolve_public_key("", PUBLIC_KEY) == PUBLIC_KEY


def test_resolve_public_key_does_not_invent_key_from_broken_private():
    broken = "не-ключ"
    assert resolve_public_key(broken, PUBLIC_KEY) == PUBLIC_KEY


def test_resolve_key_pair_leaves_private_untouched():
    private, public = resolve_key_pair(PRIVATE_KEY, foreign_public_key())
    assert private == PRIVATE_KEY
    assert public == PUBLIC_KEY


@pytest.mark.asyncio
async def test_ensure_key_pair_repairs_mismatched_row():
    async with TestingSessionLocal() as db:
        db.add(Setting(key="protocol.reality.private_key", value=PRIVATE_KEY))
        db.add(Setting(key="protocol.reality.public_key", value=foreign_public_key()))
        await db.commit()

        assert await ensure_reality_key_pair(db) is True

        rows = (
            await db.execute(
                select(Setting).where(
                    Setting.key.in_(
                        (
                            "protocol.reality.private_key",
                            "protocol.reality.public_key",
                        )
                    )
                )
            )
        ).scalars().all()
        stored = {row.key: row.value for row in rows}
        assert stored["protocol.reality.public_key"] == PUBLIC_KEY
        # Приватный ключ — источник истины, он уже в работающем конфиге Xray.
        assert stored["protocol.reality.private_key"] == PRIVATE_KEY


@pytest.mark.asyncio
async def test_ensure_key_pair_is_idempotent_on_consistent_row():
    async with TestingSessionLocal() as db:
        db.add(Setting(key="protocol.reality.private_key", value=PRIVATE_KEY))
        db.add(Setting(key="protocol.reality.public_key", value=PUBLIC_KEY))
        await db.commit()

        assert await ensure_reality_key_pair(db) is False


@pytest.mark.asyncio
async def test_ensure_key_pair_fills_missing_public_key():
    async with TestingSessionLocal() as db:
        db.add(Setting(key="protocol.reality.private_key", value=PRIVATE_KEY))
        await db.commit()

        assert await ensure_reality_key_pair(db) is True

        row = (
            await db.execute(
                select(Setting).where(Setting.key == "protocol.reality.public_key")
            )
        ).scalar_one()
        assert row.value == PUBLIC_KEY


@pytest.mark.asyncio
async def test_ensure_key_pair_without_private_key_does_nothing():
    public = PUBLIC_KEY
    async with TestingSessionLocal() as db:
        db.add(Setting(key="protocol.reality.public_key", value=public))
        await db.commit()

        assert await ensure_reality_key_pair(db) is False


@pytest.mark.asyncio
async def test_settings_expose_derived_public_key_even_with_broken_row():
    """Ссылка и конфиг должны опираться на одну пару даже с испорченной базой."""
    async with TestingSessionLocal() as db:
        db.add(Setting(key="protocol.reality.private_key", value=PRIVATE_KEY))
        db.add(Setting(key="protocol.reality.public_key", value=foreign_public_key()))
        await db.commit()

        values = await get_profile_settings(db)

        assert values["protocol.reality.public_key"] == PUBLIC_KEY
        assert values["protocol.reality.private_key"] == PRIVATE_KEY


@pytest.mark.asyncio
async def test_generated_link_matches_private_key_used_in_config():
    """Сквозная проверка: publicKey в ссылке выведен из privateKey в конфиге."""
    async with TestingSessionLocal() as db:
        db.add(Setting(key="protocol.reality.private_key", value=PRIVATE_KEY))
        db.add(Setting(key="protocol.reality.public_key", value=foreign_public_key()))
        db.add(Setting(key="protocol.reality.server_address", value="example.com"))
        db.add(Setting(key="protocol.reality.server_name", value="example.com"))
        await db.commit()

        settings = await get_profile_settings(db)
        profile = ClientProfile(kind="vless_reality_tcp", uuid="11111111-2222-3333-4444-555555555555")
        link = make_profile_data(Client(name="Тест"), profile, settings)

        assert f"pbk={PUBLIC_KEY}" in link
        assert foreign_public_key() not in link