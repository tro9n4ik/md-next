#!/usr/bin/python3
"""Install the fixed root helper and move the API to md-next. Run as root."""
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import time
import sys
import urllib.request

ROOT = Path('/opt/md-next')
LIB = Path('/usr/local/lib/md-next')
STATE = Path('/var/lib/md-next')
UNIT = Path('/etc/systemd/system/md-next-backend.service')


def command(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)


def owned_data(path, uid, gid, *, public=False):
    if path.is_symlink(): raise RuntimeError('Unexpected data directory symlink')
    path.mkdir(parents=True, exist_ok=True)
    for directory, folders, files in os.walk(path, followlinks=False):
        for item in [Path(directory), *[Path(directory)/name for name in folders+files]]:
            if item.is_symlink(): raise RuntimeError('Unexpected data symlink')
            os.chown(item, uid, gid)
            os.chmod(item, (0o755 if public else 0o750) if item.is_dir() else (0o644 if public else 0o640))


def protect_code():
    """Archive permissions must never make application code writable by the API."""
    writable = {ROOT/'backend/app/static/fake', ROOT/'backend/backups', ROOT/'data/placeholder'}
    runtimes = {ROOT/'backend/venv', ROOT/'venv-releases', ROOT/'frontend/node_modules'}
    for directory, folders, files in os.walk(ROOT, followlinks=False):
        parent = Path(directory)
        os.chown(parent, 0, 0);parent.chmod(parent.stat().st_mode & 0o777 & ~0o022)
        for name in list(folders):
            child = parent/name
            if child in writable or child in runtimes:
                folders.remove(name)
                if child in runtimes:
                    if child.is_symlink():
                        if child != ROOT/'backend/venv' or not child.resolve().is_relative_to(ROOT/'venv-releases'):
                            raise RuntimeError('Unexpected runtime symlink')
                    else:
                        os.chown(child, 0, 0);child.chmod(child.stat().st_mode & 0o777 & ~0o022)
            elif child.is_symlink(): raise RuntimeError('Unexpected code directory symlink')
        for name in files:
            child = parent/name
            if child.is_symlink():
                if child == ROOT/'backend/md_next.db' and child.resolve() == STATE/'data/md_next.db': continue
                if child == ROOT/'backend/venv' and child.resolve().is_relative_to(ROOT/'venv-releases'): continue
                raise RuntimeError('Unexpected code file symlink')
            os.chown(child, 0, 0);child.chmod(child.stat().st_mode & 0o777 & ~0o022)


def helper_sources():
    return {'privileged-helper.py': ROOT/'scripts/privileged-helper.py', 'update-panel.py': ROOT/'scripts/update-panel.py',
              'awg-routing.py': ROOT/'scripts/awg-routing.py', 'warp_registration.py': ROOT/'backend/app/services/warp_registration.py',
              'nginx.py': ROOT/'backend/app/services/nginx.py', 'app/services/shell.py': ROOT/'backend/app/services/shell.py',
              'app/services/privileges.py': ROOT/'backend/app/services/privileges.py'}

def verify_copies():
    for name, source in helper_sources().items():
        target = LIB/name
        if target.is_symlink() or target.read_bytes() != source.read_bytes():
            raise RuntimeError('Helper copy differs from release: '+name)
        metadata = target.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError('Unsafe helper ownership or permissions: '+name)


def main():
    if os.geteuid() != 0: raise RuntimeError('Root required')
    for binary in ('sudo', 'visudo'):
        if shutil.which(binary) is None: raise RuntimeError('Install sudo before migration')
    try: user = pwd.getpwnam('md-next')
    except KeyError:
        command('useradd', '--system', '--user-group', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'md-next')
        user = pwd.getpwnam('md-next')
    uid, gid = user.pw_uid, user.pw_gid
    backup = Path('/root')/('md-next-privileges-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    env_metadata = (ROOT/'backend/.env').stat()
    for name, path in [('backend.service', UNIT), ('environment', ROOT/'backend/.env')]:
        shutil.copy2(path, backup/name);(backup/name).chmod(0o600)
    protect_code()
    LIB.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chown(LIB, 0, 0);LIB.chmod(0o755)
    for relative in ('app', 'app/services'):
        (LIB/relative).mkdir(mode=0o755, exist_ok=True)
        os.chown(LIB/relative, 0, 0);(LIB/relative).chmod(0o755)
    copies = helper_sources()
    for name, source in copies.items():
        target = LIB/name
        if target.is_symlink(): raise RuntimeError('Unsafe helper destination')
        shutil.copy2(source, target);os.chown(target, 0, 0);target.chmod(0o755 if name == 'privileged-helper.py' else 0o644)
    verify_copies()
    sudoers = Path('/etc/sudoers.d/md-next')
    sudoers.write_text('md-next ALL=(root) NOPASSWD: /usr/local/lib/md-next/privileged-helper.py ""\n')
    sudoers.chmod(0o440);command('visudo', '-cf', str(sudoers))
    STATE.mkdir(mode=0o755, exist_ok=True)
    data, stage = STATE/'data', STATE/'app-config'
    for path in (data, stage):
        if path.is_symlink(): raise RuntimeError('Unexpected state directory symlink')
        path.mkdir(mode=0o700, exist_ok=True);os.chown(path, uid, gid)
    # API may create journals, but cannot replace the root-owned database with a symlink.
    os.chown(data, 0, gid);data.chmod(0o1770)
    if (data/'md_next.db').is_symlink(): raise RuntimeError('Unexpected data file symlink')
    for original, name in [(Path('/usr/local/etc/xray/config.json'), 'xray.json'), (Path('/etc/amnezia/amneziawg/awg0.conf'), 'awg0.conf')]:
        if (stage/name).is_symlink(): raise RuntimeError('Unexpected staging file symlink')
        if not (stage/name).exists(): shutil.copy2(original, stage/name)
        descriptor = os.open(stage/name, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fchown(descriptor, uid, gid);os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    fake, placeholder = ROOT/'backend/app/static/fake', ROOT/'data/placeholder'
    owned_data(fake, uid, gid, public=True);owned_data(placeholder, uid, gid)
    old_backups = ROOT/'backend/backups'
    owned_data(old_backups, uid, gid)
    command('systemctl', 'stop', 'md-next-backend')
    database = ROOT/'backend/md_next.db'
    try:
        if not database.is_symlink():
            with sqlite3.connect(database) as source, sqlite3.connect(data/'md_next.db') as target: source.backup(target)
            os.chown(data/'md_next.db', 0, gid);(data/'md_next.db').chmod(0o660)
            database.rename(backup/'original.db')
            for suffix in ('-wal', '-shm'): Path(str(database)+suffix).unlink(missing_ok=True)
            database.symlink_to(data/'md_next.db')
        elif database.resolve() != data/'md_next.db': raise RuntimeError('Unexpected database symlink')
        os.chown(data/'md_next.db', 0, gid);(data/'md_next.db').chmod(0o660)
        env = ROOT/'backend/.env'
        values = {'MDNEXT_PRIVILEGED_HELPER': '1', 'XRAY_CONFIG_PATH': str(stage/'xray.json'), 'AWG_CONFIG_PATH': str(stage/'awg0.conf')}
        lines = [line for line in env.read_text().splitlines() if line.split('=', 1)[0] not in values]
        env.write_text('\n'.join(lines+[key+'='+value for key, value in values.items()])+'\n')
        os.chown(env, 0, gid);env.chmod(0o640)
        unit = (backup/'backend.service').read_text().replace('User=root', 'User=md-next\nGroup=md-next\nUMask=0077\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nPrivateTmp=true\nProtectHome=true')
        UNIT.write_text(unit)
        route_unit = Path('/etc/systemd/system/xray.service.d/30-md-next-awg-routing.conf')
        route_unit.write_text('[Service]\nExecStartPost=-+/usr/bin/python3 /usr/local/lib/md-next/awg-routing.py --config /usr/local/etc/xray/config.json\n')
        command('systemctl', 'daemon-reload')
        if '--configure-only' not in sys.argv:
            command('systemctl', 'start', 'md-next-backend')
            for attempt in range(60):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as response:
                        if response.status == 200: break
                except Exception:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Backend did not become ready after privilege migration')
        print('BACKEND_MOVED_TO_MD_NEXT', backup)
    except Exception:
        command('systemctl', 'stop', 'md-next-backend')
        if database.is_symlink() and (backup/'original.db').exists():
            database.unlink();shutil.copy2(backup/'original.db', database)
        shutil.copy2(backup/'environment', ROOT/'backend/.env');shutil.copy2(backup/'backend.service', UNIT)
        os.chown(ROOT/'backend/.env', env_metadata.st_uid, env_metadata.st_gid)
        (ROOT/'backend/.env').chmod(env_metadata.st_mode & 0o777)
        command('systemctl', 'daemon-reload');command('systemctl', 'start', 'md-next-backend')
        raise


if __name__ == '__main__':
    if '--verify-copies' in sys.argv:
        verify_copies()
        print('Helper copies match release')
    else:
        main()
