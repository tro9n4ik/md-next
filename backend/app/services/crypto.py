import base64
import hashlib
import os
import logging
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NODE_SECRET_PREFIX = "fernet:v1:"
HOST_SECRET_PREFIX = "fernet:host:v2:"


class DecryptionError(ValueError):
    pass


@lru_cache(maxsize=1)
def machine_id():
    import importlib.util
    source = Path(__file__).with_name("host_identity.py")
    spec = importlib.util.spec_from_file_location("persistent_host_identity", source)
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    return identity.machine_id()


def _node_key(secret=None):
    secret = secret if secret is not None else os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("Для шифрования секретов требуется JWT_SECRET_KEY")
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=machine_id(),
                   info=b"mdnext/node-secret/host/v2").derive(secret.encode())
    return base64.urlsafe_b64encode(derived)


def _fernet_key(secret=None) -> bytes:
    secret = secret if secret is not None else os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("Для шифрования секретов требуется JWT_SECRET_KEY")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"md-next-secret-encryption-v1",
        info=b"fernet-key",
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def encrypt_secret(plain_text: str, *, installation_secret=None) -> str:
    if not plain_text:
        return ""
    return Fernet(_fernet_key(installation_secret)).encrypt(plain_text.encode("utf-8")).decode("ascii")


def decrypt_secret(cipher_text: str, *, installation_secret=None) -> str:
    if not cipher_text:
        return ""
    try:
        return Fernet(_fernet_key(installation_secret)).decrypt(cipher_text.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Во время перехода читаем секреты, зашифрованные прежней реализацией без HKDF.
        secret = installation_secret if installation_secret is not None else os.getenv("JWT_SECRET_KEY")
        if not secret:
            return ""
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        try:
            return Fernet(legacy_key).decrypt(cipher_text.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""
    except (ValueError, UnicodeError):
        return ""


def encrypt_node_secret(secret: str | None) -> str | None:
    if not secret:
        return secret
    return HOST_SECRET_PREFIX + Fernet(_node_key()).encrypt(secret.encode()).decode("ascii")


def decrypt_node_secret(stored: str | None, *, installation_secret=None) -> str | None:
    if stored is not None and not isinstance(stored, str):
        raise ValueError("Некорректный формат секрета ноды")
    if stored and stored.startswith(HOST_SECRET_PREFIX):
        try:
            return Fernet(_node_key(installation_secret)).decrypt(stored.removeprefix(HOST_SECRET_PREFIX).encode("ascii")).decode()
        except (InvalidToken, UnicodeError, ValueError) as error:
            raise DecryptionError("Не удалось расшифровать секрет ноды на этом сервере") from error
    if stored is None or not stored.startswith(NODE_SECRET_PREFIX):
        return stored
    secret = decrypt_secret(stored.removeprefix(NODE_SECRET_PREFIX), installation_secret=installation_secret)
    if not secret:
        raise ValueError("Не удалось расшифровать секрет ноды")
    return secret


def portable_node_secret(stored, *, installation_secret=None):
    """Используется только внутри зашифрованного переносимого комплекта."""
    plain = decrypt_node_secret(stored, installation_secret=installation_secret)
    return NODE_SECRET_PREFIX + encrypt_secret(plain, installation_secret=installation_secret) if plain else plain
