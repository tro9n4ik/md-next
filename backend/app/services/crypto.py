import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _fernet_key() -> bytes:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is required to encrypt secrets")
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"md-next-secret-encryption-v1",
        info=b"fernet-key",
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def encrypt_secret(plain_text: str) -> str:
    if not plain_text:
        return ""
    return Fernet(_fernet_key()).encrypt(plain_text.encode("utf-8")).decode("ascii")


def decrypt_secret(cipher_text: str) -> str:
    if not cipher_text:
        return ""
    try:
        return Fernet(_fernet_key()).decrypt(cipher_text.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Read secrets encrypted by the pre-HKDF implementation during migration.
        secret = os.getenv("JWT_SECRET_KEY")
        if not secret:
            return ""
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        try:
            return Fernet(legacy_key).decrypt(cipher_text.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""
    except (ValueError, UnicodeError):
        return ""
