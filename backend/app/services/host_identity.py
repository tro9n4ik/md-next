"""Постоянная идентичность установки; не выводится в журнал."""
import logging
import os
import secrets
import stat
import tempfile
from functools import lru_cache
from pathlib import Path

HOST_KEY = Path("/var/lib/md-next/host.key")
MACHINE_ID_PATHS = (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id"))


def read_key(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or (os.name == "posix" and metadata.st_mode & 0o077):
            raise RuntimeError("Небезопасный файл идентификатора установки")
        data = stream.read(257)
    parts = data.strip().split(b":")
    if len(data) > 256 or len(parts) != 3 or parts[0] != b"mdnext-host-key-v1":
        raise RuntimeError("Повреждён идентификатор установки")
    kind, value = parts[1:]
    if any(c not in b"0123456789abcdef" for c in value):
        raise RuntimeError("Повреждён идентификатор установки")
    if kind == b"machine-id" and len(value) == 32:
        return value  # Preserve the exact salt of released v2 ciphertexts.
    if kind == b"random" and len(value) == 64:
        return bytes.fromhex(value.decode("ascii"))
    raise RuntimeError("Неизвестный формат идентификатора установки")


@lru_cache(maxsize=1)
def machine_id():
    explicit = os.getenv("MDNEXT_HOST_KEY_FILE")
    if os.name != "posix" and not explicit:
        logging.getLogger(__name__).warning("Используется идентификатор локальной разработки без привязки к ОС")
        return b"mdnext-development-no-machine-id"
    path = Path(explicit) if explicit else HOST_KEY
    bridge = None
    if not explicit:
        try: bridge = read_key(path.parent/'data/host.key')
        except FileNotFoundError: pass
    try:
        canonical = read_key(path)
        if bridge is not None and bridge != canonical:
            raise RuntimeError("Конфликт идентификаторов установки")
        return canonical
    except FileNotFoundError:
        if path.is_symlink():
            raise RuntimeError("Недопустимая ссылка идентификатора установки")
    if bridge is not None:
        value = b"mdnext-host-key-v1:random:" + bridge.hex().encode('ascii') + b"\n"
        try: return publish_key(path, value)
        except PermissionError: return bridge
    value = None
    for source in MACHINE_ID_PATHS:
        try:
            candidate = source.read_text().strip()
        except OSError:
            continue
        if len(candidate) == 32 and all(c in "0123456789abcdef" for c in candidate):
            value = b"mdnext-host-key-v1:machine-id:" + candidate.encode() + b"\n"
            break
    if value is None:
        value = b"mdnext-host-key-v1:random:" + secrets.token_hex(32).encode() + b"\n"
    try:
        result = publish_key(path, value)
    except PermissionError:
        if explicit:
            raise
        # Legacy updaters run Alembic as md-next before the new root installer.
        # The database directory already belongs to that service's writable state.
        result = publish_key(path.parent/'data/host.key', value)
        try:
            canonical = read_key(path)
        except FileNotFoundError:
            return result
        if canonical != result:
            raise RuntimeError("Конфликт идентификаторов установки")
    return result


def publish_key(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".host-key-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # Atomic publication without replacing another starter's key.
        except FileExistsError:
            pass
    finally:
        Path(temporary).unlink(missing_ok=True)
    if os.name == "posix":
        fd = os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    return read_key(path)
