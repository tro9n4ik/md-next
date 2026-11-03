#!/usr/bin/env python3
"""Root-only target preparation. Raw output stays in a bounded private log."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

spec = importlib.util.spec_from_file_location('migration_events', Path(__file__).with_name('migration-events.py'))
events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(events)
STATE = Path('/var/lib/md-next/migration')
ROOT = Path('/opt/md-next')
LOG_LIMIT = 1024 * 1024


def emit(code):
    print(events.event(code), flush=True)


def save_marker(data):
    fd, name = tempfile.mkstemp(prefix='.prepare-', dir=STATE)
    with os.fdopen(fd, 'w') as output:
        json.dump(data, output)
        output.flush(); os.fsync(output.fileno())
    os.chmod(name, 0o600)
    os.replace(name, STATE/'prepare.json')
    if os.name=='posix':
        directory=os.open(STATE,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)


def marker_matches(data, commit, source):
    return isinstance(data, dict) and data.get('commit') == commit and data.get('source') == source


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def check_empty_staging():
    if ROOT.is_symlink() or (ROOT/'backend/.env').is_symlink():
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    if any((STATE/name).exists() for name in ('restored.json','activated','importing.json')):
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    env = ROOT/'backend/.env'
    if ROOT.exists() and (not env.is_file() or 'MDNEXT_MIGRATION_STAGING=1' not in env.read_text().splitlines()):
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    database = ROOT/'backend/md_next.db'
    if database.is_symlink() and database.resolve() != STATE.parent/'data/md_next.db':
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    if database.exists():
        with contextlib.closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('clients', 'client_profiles', 'nodes'):
                if table in tables and db.execute('SELECT count(*) FROM '+table).fetchone()[0]:
                    raise events.MigrationError('MIG_TARGET_OCCUPIED')


def run(command, code, *, timeout=1800, env=None, input_text=None):
    log_path = STATE/'prepare.log'
    if log_path.is_symlink():
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'ab') as log:
        command_start=log_path.stat().st_size
        # A file, rather than a pipe, is deliberate: on timeout/disconnect dpkg
        # must not receive SIGPIPE or block because its reader disappeared.
        process = subprocess.Popen(command, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            if input_text is not None:
                process.stdin.write(input_text.encode()); process.stdin.close()
            deadline = time.monotonic()+timeout
            pending = b''
            read_fd = os.open(log_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(read_fd, 'rb', buffering=0) as reader:
                # Start at this command's first output, including fast children.
                reader.seek(command_start)
                while True:
                    if time.monotonic()>deadline:raise events.MigrationError('MIG_TIMEOUT')
                    chunk = reader.read(4096)
                    if chunk:
                        pending += chunk
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            item = events.parse_event(line.decode('utf-8', 'replace'))
                            if item and item['code'].startswith('INSTALL_'):
                                emit(item['code'])
                        if len(pending)>1024:pending=b''
                        continue
                    if log_path.stat().st_size>LOG_LIMIT:
                        log.seek(0);log.truncate();reader.seek(0)
                    if process.poll() is not None:break
                    time.sleep(.1)
            if process.returncode:
                raise events.MigrationError(code,exit_code=process.returncode)
        finally:
            if process.poll() is None and apt_unlocked():
                import signal
                os.killpg(process.pid,signal.SIGTERM)



def apt_unlocked():
    import fcntl
    for name in ('/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock', '/var/lib/apt/lists/lock'):
        try:
            with open(name, 'r+') as lock:
                fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except FileNotFoundError:
            continue
        except BlockingIOError:
            return False
    return True


def wait_apt(timeout=900):
    emit('TARGET_APT_WAIT')
    deadline = time.monotonic()+timeout
    while not apt_unlocked():
        if time.monotonic() > deadline:
            raise events.MigrationError('MIG_APT_LOCK')
        time.sleep(3)


def verify_build(commit):
    def api(path):
        request = urllib.request.Request('https://api.github.com/repos/tro9n4ik/md-next/'+path,
                                         headers={'User-Agent': 'MD-Next-migration'})
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    try:
        data = api('commits/'+commit)
        if data['sha'] != commit or not data['commit']['verification']['verified']:
            raise ValueError()
        runs = api('actions/runs?head_sha='+commit+'&per_page=100')['workflow_runs']
        for path in ('.github/workflows/ci.yml', '.github/workflows/secrets.yml'):
            matching = [r for r in runs if r['path'] == path and r['event'] == 'push']
            if not matching or max(matching, key=lambda r: r['id'])['conclusion'] != 'success':
                raise ValueError()
    except Exception as error:
        raise events.MigrationError('MIG_BUILD') from error


def preflight(commit, source):
    emit('TARGET_PREFLIGHT')
    values = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    versions = {'debian': {'12', '13'}, 'ubuntu': {'22.04', '24.04', '26.04'}}
    if values.get('VERSION_ID', '').strip('"') not in versions.get(values.get('ID', '').strip('"'), set()):
        raise events.MigrationError('MIG_TARGET_OS')
    if os.uname().machine not in {'x86_64', 'aarch64'}:
        raise events.MigrationError('MIG_TARGET_OS')
    memory = re.findall(r'(?m)^(?:MemTotal|SwapTotal):\s+(\d+)', Path('/proc/meminfo').read_text())
    if sum(map(int, memory)) < 2097152 or shutil.disk_usage('/opt').free < 5*1024**3:
        raise events.MigrationError('MIG_TARGET_RESOURCES')
    marker = STATE/'prepare.json'
    if marker.is_symlink() or marker.exists() and (marker.stat().st_uid!=0 or marker.stat().st_mode & 0o022):
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    previous = json.loads(marker.read_text()) if marker.is_file() else None
    if previous is not None and not marker_matches(previous, commit, source):
        # A cancelled, empty staging installation may advance to a new signed
        # build of the same source. Never adopt another source or client data.
        if previous.get('source')!=source or previous.get('phase')!='installed':
            raise events.MigrationError('MIG_TARGET_BINDING')
    if previous is not None:
        check_empty_staging()
    occupied = ROOT.exists() or ROOT.is_symlink() or Path('/var/lib/md-next/data/md_next.db').exists()
    services = any(subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode == 0 for service in ('nginx', 'xray'))
    if (occupied or services) and previous is None:
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    if previous is not None and previous.get('phase') in {'installing', 'installed'}:
        for service in ('md-next-backend', 'md-next-mtproto', 'xray', 'md-next-awg', 'nginx'):
            subprocess.run(['systemctl', 'stop', service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        emit('TARGET_RESUME')
    ports = subprocess.check_output(['ss', '-H', '-lnt'], text=True)
    if any(re.search(r':(?:80|443|8000)$', row.split()[3]) for row in ports.splitlines() if len(row.split()) >= 4):
        raise events.MigrationError('MIG_TARGET_PORTS')
    return previous


def prepare(commit, main_domain, panel_domain, email, source):
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9a-f]{64}', source):
        raise events.MigrationError('MIG_TARGET_BINDING')
    label = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    domain_pattern = r'(?=.{1,253}$)(?:'+label+r'\.)+'+label
    if any(not re.fullmatch(domain_pattern, d) for d in (main_domain, panel_domain)) or email and not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', email):
        raise events.MigrationError('MIG_SOURCE_ADDRESS')
    verify_build(commit)
    previous = preflight(commit, source)
    if previous and previous.get('phase')=='reboot_required' and previous.get('boot_id')==boot_id():
        raise events.MigrationError('MIG_REBOOT_REQUIRED')
    marker = {'commit': commit, 'source': source, 'phase': 'updating'}
    if previous and previous.get('phase') == 'installed' and previous.get('commit')==commit:
        if (ROOT/'DEPLOYED_COMMIT').read_text().strip() != commit:
            raise events.MigrationError('MIG_BUILD')
        emit('TARGET_READY'); return
    save_marker(marker)
    timers = [name for name in ('apt-daily.timer', 'apt-daily-upgrade.timer')
              if subprocess.run(['systemctl', 'is-active', '--quiet', name]).returncode == 0]
    try:
        for timer in timers:
            subprocess.run(['systemctl', 'stop', timer], check=True)
        wait_apt()
        emit('TARGET_APT_UPDATE')
        run(['apt-get', '-o', 'DPkg::Lock::Timeout=900', 'update', '-qq'], 'MIG_APT_UPDATE', timeout=900)
        emit('TARGET_APT_UPGRADE')
        environment = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'a'}
        run(['apt-get', '-o', 'DPkg::Lock::Timeout=900', '-o', 'Dpkg::Options::=--force-confdef',
             '-o', 'Dpkg::Options::=--force-confold', 'dist-upgrade', '-y', '-qq'], 'MIG_APT_UPDATE', env=environment)
        if Path('/var/run/reboot-required').exists():
            marker.update(phase='reboot_required',boot_id=boot_id()); save_marker(marker)
            raise events.MigrationError('MIG_REBOOT_REQUIRED')
        run(['apt-get', '-o', 'DPkg::Lock::Timeout=900', 'install', '-y', '-qq', 'git', 'python3', 'ca-certificates'],
            'MIG_APT_UPDATE', env=environment)
        emit('TARGET_BUILD'); verify_build(commit)
        with tempfile.TemporaryDirectory(prefix='mdnext-target-', dir='/root') as work:
            checkout = Path(work)/'source'
            run(['git', 'clone', '--quiet', '--no-checkout', 'https://github.com/tro9n4ik/md-next.git', str(checkout)], 'MIG_BUILD', timeout=300)
            run(['git', '-C', str(checkout), 'checkout', '--quiet', '--detach', commit], 'MIG_BUILD', timeout=60)
            marker['phase'] = 'installing'; save_marker(marker); emit('TARGET_INSTALL')
            run(['bash', str(checkout/'scripts/install.sh'), '1'], 'MIG_INSTALL', timeout=2400,
                env={**environment, 'MDNEXT_MIGRATION_STAGING': '1', 'MDNEXT_TEST_SELF_SIGNED': '1', 'MDNEXT_PROGRESS': '1'},
                input_text='\n'.join((main_domain, panel_domain, email))+'\n')
        if not (ROOT/'DEPLOYED_COMMIT').is_file() or (ROOT/'DEPLOYED_COMMIT').read_text().strip()!=commit:
            raise events.MigrationError('MIG_BUILD')
        check_empty_staging()
        marker['phase'] = 'installed'; save_marker(marker); emit('TARGET_READY')
    finally:
        for timer in timers:
            subprocess.run(['systemctl', 'start', timer], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for service in ('md-next-backend', 'md-next-mtproto', 'xray', 'md-next-awg'):
            subprocess.run(['systemctl', 'stop', service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    import fcntl
    if os.geteuid() != 0 or len(sys.argv) != 6:
        raise events.MigrationError('MIG_STATE')
    if any(parent.is_symlink() for parent in (STATE, *STATE.parents)):
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.stat().st_uid != 0:
        raise events.MigrationError('MIG_TARGET_OCCUPIED')
    STATE.chmod(0o700)
    with (STATE/'prepare.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(*sys.argv[1:])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        emit(events.public_error(error)['code'])
        raise SystemExit(75 if getattr(error, 'code', '') == 'MIG_REBOOT_REQUIRED' else 1)
