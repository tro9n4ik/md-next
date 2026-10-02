"""Согласование пары ключей Reality.

Приватный ключ попадает в конфиг Xray, а публичный — в ссылки клиентов. Если пара
рассинхронизирована, сервер не узнаёт клиента на этапе handshake и отдаёт ему по
``dest`` настоящий сертификат вместо ответа Reality. Клиент это видит как
``REALITY: received real certificate (potential MITM or redirection)`` и рвёт
соединение, при том что панель, балансер и нода полностью здоровы и нигде не
светят ошибкой.

Источник истины — приватный ключ: он уже в работающем конфиге, и клиенты держат
ссылки с его производным публичным ключом. Поэтому публичный всегда выводится из
приватного, а не берётся «как есть».
"""

import base64
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.setting import Setting

logger = logging.getLogger(__name__)

PRIVATE_KEY_SETTING = "protocol.reality.private_key"
PUBLIC_KEY_SETTING = "protocol.reality.public_key"


def derive_public_key(private_key: str) -> str:
    """Выводит публичный ключ X25519 в формате Xray: base64url без padding."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    value = (private_key or "").strip()
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    public = x25519.X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(public).decode("ascii").rstrip("=")


def resolve_public_key(private_key: str, public_key: str) -> str:
    """Возвращает публичный ключ, гарантированно соответствующий приватному.

    Если приватный ключ пуст или нечитаем, возвращает исходное значение: в этом
    случае чинить нечего, и молча подменять настройку опаснее, чем оставить её.
    """
    if not (private_key or "").strip():
        return public_key or ""
    try:
        return derive_public_key(private_key)
    except Exception as exc:  # noqa: BLE001 - причина в лог, значение не теряем
        logger.warning("Не удалось вывести публичный ключ Reality из приватного: %s", exc)
        return public_key or ""


def resolve_key_pair(private_key: str, public_key: str) -> tuple[str, str]:
    """Возвращает согласованную пару ключей."""
    resolved = resolve_public_key(private_key, public_key)
    return private_key or "", resolved


async def ensure_reality_key_pair(db: AsyncSession) -> bool:
    """Приводит пару ключей и параметры сервера в базе к согласованному виду.

    Возвращает ``True``, если пара или параметры были исправлены. Вызывается на старте панели.
    """
    import os
    keys_to_check = (
        PRIVATE_KEY_SETTING,
        PUBLIC_KEY_SETTING,
        "protocol.reality.server_address",
        "protocol.reality.server_name",
    )
    result = await db.execute(
        select(Setting).where(Setting.key.in_(keys_to_check))
    )
    rows = {row.key: row for row in result.scalars().all()}

    changed = False
    private_row = rows.get(PRIVATE_KEY_SETTING)
    public_row = rows.get(PUBLIC_KEY_SETTING)
    private_key = private_row.value if private_row else ""
    public_key = public_row.value if public_row else ""

    corrected = resolve_public_key(private_key, public_key)
    if corrected and corrected != public_key:
        if public_row is None:
            db.add(Setting(key=PUBLIC_KEY_SETTING, value=corrected))
        else:
            public_row.value = corrected
        changed = True
        logger.warning(
            "Публичный ключ Reality не соответствует приватному: исправлено %s -> %s.",
            (public_key or "")[:12],
            corrected[:12],
        )

    server_host_env = os.getenv("SERVER_HOST", "").strip()
    if server_host_env and server_host_env not in ("127.0.0.1", "localhost"):
        addr_row = rows.get("protocol.reality.server_address")
        if addr_row and addr_row.value in ("127.0.0.1", "localhost"):
            addr_row.value = server_host_env
            changed = True

    server_name_env = os.getenv("XRAY_SERVER_NAME", "").strip()
    if server_name_env:
        sni_row = rows.get("protocol.reality.server_name")
        if sni_row and not sni_row.value:
            sni_row.value = server_name_env
            changed = True

    if changed:
        await db.commit()

    return changed