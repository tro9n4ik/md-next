#!/usr/bin/python3
"""Remove only stale MD-Next build caches; never application data or backups."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import time

CACHE = Path('/var/cache/md-next')
STATE = Path('/var/lib/md-next')
LIB = Path('/usr/local/lib/md-next')
UNITS = Path('/etc/systemd/system')
MAX_AGE = 7 * 24 * 60 * 60
OWNER_UID = 0

SERVICE = '''[Unit]
Description=Clean stale MD-Next build caches
[Service]
Type=oneshot
User=root
UMask=0077
ExecStart=/usr/bin/python3 /usr/local/lib/md-next/disk-maintenance.py
Nice=19
IOSchedulingClass=idle
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/cache/md-next /var/lib/md-next
TimeoutStartSec=300
'''
TIMER = '''[Unit]
Description=Daily MD-Next cache maintenance
[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
'''


def safe_metadata(info):
    return info.st_uid == OWNER_UID and not info.st_mode & 0o022


def trusted_directory(path):
    """Reject symlinked/writable ancestors before root creates or traverses paths."""
    path = Path(path)
    for current in reversed((path, *path.parents)):
        if not current.exists() and not current.is_symlink():
            current.mkdir(mode=0o700)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or not safe_metadata(info):
            raise RuntimeError('Unsafe maintenance directory: ' + str(current))
    return path


@contextmanager
def maintenance_lock(state=STATE):
    import fcntl
    trusted_directory(state)
    fd = os.open(state / 'maintenance.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not safe_metadata(info):
            raise RuntimeError('Unsafe maintenance lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)


def clean_cache(root, *, now=None, dry_run=False):
    """Root-owned private tree only. No symlink following, mount crossing or rmtree."""
    now = time.time() if now is None else now
    total = count = skipped = 0
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or not safe_metadata(info):
        raise RuntimeError('Unsafe cache root')
    def visit(directory):
        nonlocal total, count, skipped
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    item = os.stat(entry.path, follow_symlinks=False)
                    if item.st_dev != info.st_dev or not safe_metadata(item):
                        skipped += 1
                        continue
                    if stat.S_ISDIR(item.st_mode):
                        visit(Path(entry.path))
                        if not dry_run and item.st_mtime < now - MAX_AGE:
                            try:
                                os.rmdir(entry.path)
                            except OSError:
                                pass  # A non-empty directory is intentionally retained.
                    elif stat.S_ISREG(item.st_mode) and item.st_mtime < now - MAX_AGE:
                        if not dry_run:
                            os.unlink(entry.path)
                        total += item.st_size
                        count += 1
                    elif not stat.S_ISREG(item.st_mode):
                        skipped += 1
                except FileNotFoundError:
                    continue
    visit(root)
    return {'files': count, 'bytes': total, 'skipped': skipped, 'dry_run': dry_run}


def install():
    timer_path = UNITS / 'md-next-cache-clean.timer'
    if timer_path.is_symlink() and os.readlink(timer_path) == '/dev/null':
        print('Maintenance timer is masked; leaving it disabled')
        return
    existing_timer = timer_path.exists()
    for path in (CACHE, STATE, LIB):
        trusted_directory(path)
    destination = LIB / 'disk-maintenance.py'
    if destination.is_symlink():
        raise RuntimeError('Unsafe maintenance script destination')
    if Path(__file__).resolve() != destination:
        # Atomic replacement; never truncate a pre-existing hardlink.
        temporary = LIB / 'disk-maintenance.next'
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                target.write(Path(__file__).read_bytes())
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    for name, content in (('md-next-cache-clean.service', SERVICE), ('md-next-cache-clean.timer', TIMER)):
        destination = UNITS / name
        if destination.is_symlink():
            raise RuntimeError('Unsafe maintenance unit destination')
        temporary = UNITS / (name + '.next')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            with os.fdopen(fd, 'w') as target:
                target.write(content)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if not existing_timer:
        subprocess.run(['systemctl', 'enable', '--now', 'md-next-cache-clean.timer'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--prepare-cache', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('Root required')
    if args.prepare_cache:
        trusted_directory(STATE)
        for name in ('pip', 'npm', 'tmp'):
            trusted_directory(CACHE / name)
        return
    if args.install:
        install()
        return
    with maintenance_lock() as acquired:
        if not acquired:
            print(json.dumps({'skipped': 'update_or_migration_running'}))
            return
        trusted_directory(CACHE)
        result = clean_cache(CACHE, dry_run=args.dry_run)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
