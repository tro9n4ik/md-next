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


@pytest.mark.skipif(os.name != 'posix',reason='Legacy Linux updater')
@pytest.mark.parametrize('with_machine_id',[True,False])
def test_legacy_unprivileged_upgrade_then_root_promotion_preserves_key(tmp_path,monkeypatch,with_machine_id):
    canonical = tmp_path/'state/host.key'
    canonical.parent.mkdir();(canonical.parent/'data').mkdir()
    source = tmp_path/'machine-id'
    if with_machine_id:source.write_text('1'*32)
    monkeypatch.delenv('MDNEXT_HOST_KEY_FILE',raising=False)
    monkeypatch.setattr(identity,'HOST_KEY',canonical)
    monkeypatch.setattr(identity,'MACHINE_ID_PATHS',(source,))
    publish=identity.publish_key
    privileged=[False]
    def write(path,value):
        if path==canonical and not privileged[0]:raise PermissionError()
        return publish(path,value)
    monkeypatch.setattr(identity,'publish_key',write)
    identity.machine_id.cache_clear()
    first=identity.machine_id()
    assert (canonical.parent/'data/host.key').stat().st_mode&0o777 == 0o600
    assert not canonical.exists()
    if with_machine_id:assert first==b'1'*32
    source.write_text('2'*32)
    privileged[0]=True;identity.machine_id.cache_clear()
    assert identity.machine_id()==first
    assert identity.read_key(canonical)==first
    identity.machine_id.cache_clear()


@pytest.mark.skipif(os.name != 'posix',reason='Linux identity bridge')
def test_disagreeing_bridge_and_canonical_fail_closed(tmp_path,monkeypatch):
    canonical=tmp_path/'host.key';bridge=tmp_path/'data/host.key'
    identity.publish_key(canonical,b'mdnext-host-key-v1:machine-id:'+b'1'*32+b'\n')
    identity.publish_key(bridge,b'mdnext-host-key-v1:machine-id:'+b'2'*32+b'\n')
    monkeypatch.delenv('MDNEXT_HOST_KEY_FILE',raising=False)
    monkeypatch.setattr(identity,'HOST_KEY',canonical);identity.machine_id.cache_clear()
    with pytest.raises(RuntimeError,match='Конфликт'):identity.machine_id()
    identity.machine_id.cache_clear()
