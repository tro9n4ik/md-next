from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from app.services import host_identity as identity, crypto


@pytest.fixture
def volume(tmp_path, monkeypatch):
    path = tmp_path / "volume" / "host.key"
    monkeypatch.setenv("MDNEXT_HOST_KEY_FILE", str(path))
    source = tmp_path / "machine-id"
    monkeypatch.setattr(identity, "MACHINE_ID_PATHS", (source,))
    identity.machine_id.cache_clear()
    yield path, source
    identity.machine_id.cache_clear()


def test_recreation_keeps_released_v2_salt(volume, monkeypatch):
    path, source = volume
    source.write_text("1" * 32)
    monkeypatch.setattr(crypto, "machine_id", identity.machine_id)
    stored = crypto.encrypt_node_secret("synthetic")
    assert identity.machine_id() == b"1" * 32
    source.write_text("2" * 32)
    identity.machine_id.cache_clear()
    assert crypto.decrypt_node_secret(stored) == "synthetic"
    assert path.is_file()


def test_random_seed_publication_is_atomic(volume):
    path, _ = volume
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: identity.machine_id.__wrapped__(), range(20)))
    assert len(set(results)) == 1 and len(results[0]) == 32
    assert not list(path.parent.glob(".host-key-*"))
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_corrupt_identity_is_not_replaced(volume):
    path, _ = volume
    path.parent.mkdir()
    path.write_text("invalid")
    path.chmod(0o600)
    with pytest.raises(RuntimeError):
        identity.machine_id()
    assert path.read_text() == "invalid"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_identity_rejects_symlinks_and_public_permissions(volume):
    path, source = volume
    path.parent.mkdir()
    source.write_text("1" * 32)
    path.symlink_to(source)
    with pytest.raises(OSError):
        identity.machine_id()
    path.unlink()
    path.write_bytes(b"mdnext-host-key-v1:machine-id:" + b"1" * 32)
    path.chmod(0o644)
    with pytest.raises(RuntimeError):
        identity.machine_id()
