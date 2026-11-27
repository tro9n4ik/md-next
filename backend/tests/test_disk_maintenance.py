"""Deletion is scoped, age-limited and mutually exclusive with maintenance."""
import importlib.util
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def cleaner(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/disk-maintenance.py'
    spec = importlib.util.spec_from_file_location('disk_maintenance_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'OWNER_UID', os.getuid() if hasattr(os, 'getuid') else 0)
    return module


@pytest.fixture
def cache(cleaner, tmp_path, monkeypatch):
    path = tmp_path / 'cache'
    path.mkdir(mode=0o700)
    if sys.platform == 'win32':
        # Windows has no POSIX ownership/mode semantics; test them separately.
        monkeypatch.setattr(cleaner, 'safe_metadata', lambda info: True)
    return path


def aged(path, age, now=2_000_000_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'cache payload')
    path.chmod(0o600)
    os.utime(path, (now-age, now-age))
    return path


def test_only_old_cache_files_deleted(cleaner, cache, tmp_path):
    old = aged(cache/'pip'/'old', cleaner.MAX_AGE+1)
    fresh = aged(cache/'npm'/'fresh', 10)
    boundary = aged(cache/'tmp'/'boundary', cleaner.MAX_AGE)
    # Neither neighbouring application data nor backups are traversed.
    data = aged(tmp_path/'data'/'md_next.db', cleaner.MAX_AGE*2)
    backup = aged(tmp_path/'backup'/'application.tar.gz', cleaner.MAX_AGE*2)
    result = cleaner.clean_cache(cache, now=2_000_000_000)
    assert result['files'] == 1 and result['bytes'] == len(b'cache payload')
    assert not old.exists()
    assert all(p.exists() for p in (fresh, boundary, data, backup))


def test_dry_run_reports_without_deleting(cleaner, cache):
    old = aged(cache/'old', cleaner.MAX_AGE+1)
    result = cleaner.clean_cache(cache, now=2_000_000_000, dry_run=True)
    assert result['files'] == 1 and result['dry_run'] is True
    assert old.exists()


def test_symlinks_are_not_followed(cleaner, cache, tmp_path):
    external = aged(tmp_path/'outside'/'secret', cleaner.MAX_AGE*2)
    try:
        (cache/'linked-directory').symlink_to(external.parent, target_is_directory=True)
        (cache/'linked-file').symlink_to(external)
    except OSError:
        pytest.skip('OS does not permit test symlinks')
    result = cleaner.clean_cache(cache, now=2_000_000_000)
    assert result['files'] == 0 and external.exists()
    assert (cache/'linked-file').is_symlink()


def test_symlink_cache_root_rejected(cleaner, cache, tmp_path):
    alias = tmp_path/'alias'
    try:
        alias.symlink_to(cache, target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit test symlinks')
    with pytest.raises(RuntimeError, match='Unsafe cache root'):
        cleaner.clean_cache(alias)


@pytest.mark.parametrize('mode,uid,allowed', [(0o700,0,True), (0o755,0,True), (0o777,0,False), (0o775,0,False), (0o700,1000,False)])
def test_unsafe_ownership_and_permissions_rejected(cleaner, monkeypatch, mode, uid, allowed):
    monkeypatch.setattr(cleaner, 'OWNER_UID', 0)
    assert cleaner.safe_metadata(SimpleNamespace(st_uid=uid, st_mode=stat.S_IFDIR|mode)) is allowed


@pytest.mark.skipif(os.name == 'nt', reason='flock is a Linux kernel primitive')
def test_update_or_migration_lock_prevents_cleanup(cleaner, cache, monkeypatch):
    monkeypatch.setattr(cleaner, 'trusted_directory', lambda path: path)
    with cleaner.maintenance_lock(cache) as first:
        assert first is True
        with cleaner.maintenance_lock(cache) as second:
            assert second is False
    with cleaner.maintenance_lock(cache) as released:
        assert released is True


def test_install_enables_new_timer_but_preserves_existing_disabled_timer(cleaner, tmp_path, monkeypatch):
    if os.name == 'nt':
        # This test checks unit installation/idempotency, not Linux open flags.
        monkeypatch.setattr(cleaner.os, 'O_NOFOLLOW', 0, raising=False)
    for name in ('CACHE', 'STATE', 'LIB', 'UNITS'):
        path = tmp_path/name
        path.mkdir()
        monkeypatch.setattr(cleaner, name, path)
    monkeypatch.setattr(cleaner, 'trusted_directory', lambda path: path)
    commands = []
    monkeypatch.setattr(cleaner.subprocess, 'run', lambda command, **kwargs: commands.append(command))
    cleaner.install()
    assert ['systemctl', 'enable', '--now', 'md-next-cache-clean.timer'] in commands
    assert (cleaner.LIB/'disk-maintenance.py').read_bytes() == Path(cleaner.__file__).read_bytes()
    assert 'ProtectSystem=strict' in (cleaner.UNITS/'md-next-cache-clean.service').read_text()
    commands.clear()
    cleaner.install()
    assert commands == [['systemctl', 'daemon-reload']]


def test_nested_mount_is_not_traversed(cleaner, cache, monkeypatch):
    retained = aged(cache/'other-filesystem'/'old', cleaner.MAX_AGE*2)
    real_stat = cleaner.os.stat
    def mocked_stat(path, **kwargs):
        result = real_stat(path, **kwargs)
        if Path(path) == retained.parent:
            values = {name: getattr(result, name) for name in ('st_mode', 'st_uid', 'st_dev', 'st_mtime', 'st_size')}
            values['st_dev'] += 1
            return SimpleNamespace(**values)
        return result
    monkeypatch.setattr(cleaner.os, 'stat', mocked_stat)
    assert cleaner.clean_cache(cache, now=2_000_000_000)['files'] == 0
    assert retained.exists()
