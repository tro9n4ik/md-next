#!/usr/bin/env python3
"""First-party application updater. No OS/package/VPN installer is executed."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path('/opt/md-next')
STATE = Path('/var/lib/md-next/updates')
CONFIGS = [Path('/usr/local/etc/xray/config.json'), Path('/etc/amnezia/amneziawg/awg0.conf')]


def environment(root):
    result = dict(os.environ)
    for line in (root / 'backend/.env').read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def snapshot_database(source, target):
    with sqlite3.connect(source) as live, sqlite3.connect(target) as saved:
        live.backup(saved)
    target.chmod(0o600)


def identities(root):
    with sqlite3.connect(root / 'backend/md_next.db') as db:
        return db.execute('SELECT id,uuid FROM clients ORDER BY id').fetchall()


def active_awg_units():
    units = []
    for unit in ('md-next-awg.service', 'awg-quick@awg0.service'):
        result = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip() == 'active':
            units.append(unit)
    return units


class Updater:
    def __init__(self, root, state, request):
        self.root, self.state, self.request = root, state, request
        self.data = {'commit': request['commit'], 'started_at': time.time()}
        self.backup = None
        self.activated = False
        self.old_venv = None
        self.new_venv = None
        self.log = None
        self.awg_units = []

    def phase(self, phase, message):
        self.data.update(phase=phase, message=message)
        if self.backup:
            self.data['backup'] = str(self.backup)
        if phase in ('success', 'error', 'rolled_back', 'rollback_failed'):
            self.data['finished_at'] = time.time()
        temporary = self.state / 'status.next'
        temporary.write_text(json.dumps(self.data, ensure_ascii=False))
        temporary.chmod(0o600)
        temporary.replace(self.state / 'status.json')

    def run(self, args, cwd=None, env=None, timeout=300):
        return subprocess.run(args, cwd=cwd, env=env, stdout=self.log, stderr=subprocess.STDOUT, check=True, timeout=timeout)

    def verify_revision(self, token):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "MD-Next-updater"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(
            "https://api.github.com/repos/tro9n4ik/md-next/commits/" + self.request["commit"],
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            revision = json.load(response)
        if revision.get("sha") != self.request["commit"] or revision.get("commit", {}).get("verification", {}).get("verified") is not True:
            raise RuntimeError("Подпись загружаемой версии не подтверждена GitHub")

    def prepare(self, directory):
        self.phase('preparing', 'Скачиваем проверенную сборку и готовим зависимости. Панель продолжает работать.')
        repository = directory / 'repository'
        git_env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
        token = self.request.pop('token', '')
        # Verify before fetching or executing anything from the new repository.
        self.verify_revision(token)
        if token:
            askpass = directory / 'askpass.py'
            askpass.write_text('#!/usr/bin/python3\nimport os,sys\nprint("x-access-token" if "username" in sys.argv[1].lower() else os.environ["MDNEXT_UPDATE_TOKEN"])\n')
            askpass.chmod(0o700)
            git_env.update(GIT_ASKPASS=str(askpass), MDNEXT_UPDATE_TOKEN=token)
        self.run(['git', 'init', str(repository)])
        self.run(['git', '-C', str(repository), 'remote', 'add', 'origin', 'https://github.com/tro9n4ik/md-next.git'])
        self.run(['git', '-C', str(repository), '-c', 'credential.helper=', 'fetch', '--depth=1', 'origin', self.request['commit']], env=git_env, timeout=180)
        git_env.pop('MDNEXT_UPDATE_TOKEN', None)
        self.run(['git', '-C', str(repository), 'checkout', '--detach', 'FETCH_HEAD'])
        actual = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != self.request['commit']:
            raise RuntimeError('Downloaded revision differs from requested revision')
        self.new_venv = self.root / 'venv-releases' / ('update-' + actual[:12] + '-' + str(int(time.time())))
        self.new_venv.parent.mkdir(exist_ok=True)
        self.run([self.request['python'], '-m', 'venv', str(self.new_venv)])
        python = self.new_venv / 'bin/python'
        self.run([str(python), '-m', 'pip', 'install', '-r', str(repository / 'backend/requirements.txt')], timeout=900)
        self.run([str(python), '-c', 'import app.main'], cwd=repository / 'backend', env=environment(self.root))
        self.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=repository / 'frontend', timeout=600)
        self.run(['npm', 'run', 'build'], cwd=repository / 'frontend', timeout=300)
        return repository

    def save(self):
        self.awg_units = active_awg_units()
        self.backup = self.root.parent / ('md-next-update-backup-' + time.strftime('%Y%m%d-%H%M%S'))
        self.backup.mkdir(mode=0o700)
        self.phase('backup', 'Создаём копию приложения, базы и конфигураций перед обновлением.')
        def include(info):
            parts = Path(info.name).parts
            if any(part in {'.git', 'node_modules', 'venv', 'venv-releases', '__pycache__', 'backups', '.pytest_cache'} for part in parts) or Path(info.name).name in {'md_next.db', 'md_next.db-wal', 'md_next.db-shm'}:
                return None
            return info
        with tarfile.open(self.backup / 'application.tar.gz', 'w:gz') as archive:
            archive.add(self.root, arcname='.', filter=include)
        with tarfile.open(self.backup / 'vpn-configurations.tar.gz', 'w:gz') as archive:
            for index, source in enumerate(CONFIGS):
                if source.exists():
                    archive.add(source, arcname=str(index))
        for path in self.backup.iterdir():
            path.chmod(0o600)
        snapshot_database(self.root / 'backend/md_next.db', self.backup / 'md_next.db')

    def activate(self, repository):
        self.phase('installing', 'Применяем обновление. Панель кратковременно перезапускается.')
        self.activated = True
        self.run(['systemctl', 'stop', 'md-next-backend'])
        snapshot_database(self.root / 'backend/md_next.db', self.backup / 'md_next.db')
        self.saved_identities = identities(self.root)
        venv = self.root / 'backend/venv'
        if venv.is_symlink():
            self.old_venv = venv.resolve()
            venv.unlink()
        elif venv.is_dir():
            self.old_venv = self.root / 'venv-releases' / ('previous-' + str(int(time.time())))
            venv.rename(self.old_venv)
        else:
            raise RuntimeError('Previous runtime missing')
        venv.symlink_to(self.new_venv, target_is_directory=True)
        files = subprocess.check_output(['git', '-C', str(repository), 'ls-files', '-z']).decode().split('\0')
        preserve_placeholder = (self.root / 'backend/app/static/fake/index.html').exists()
        for name in filter(None, files):
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or relative.name == '.env' or any(part in {'venv', 'node_modules', 'venv-releases', 'backups'} for part in relative.parts):
                raise RuntimeError('Unsafe source path')
            source, destination = repository / relative, self.root / relative
            if source.is_symlink() or destination.is_symlink():
                raise RuntimeError('Unexpected source symlink')
            # Публичная страница — пользовательские данные, а не обновляемый шаблон.
            if relative.parts[:4] == ('backend', 'app', 'static', 'fake') and preserve_placeholder:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self.run([str(venv / 'bin/python'), '-m', 'alembic', 'upgrade', 'head'], cwd=self.root / 'backend', env=environment(self.root), timeout=180)
        shutil.copytree(repository / 'frontend/dist/assets', self.root / 'frontend/dist/assets', dirs_exist_ok=True)
        index = self.root / 'frontend/dist/index.html'
        shutil.copy2(repository / 'frontend/dist/index.html', index.with_suffix('.next'))
        index.with_suffix('.next').replace(index)
        self.run(['systemctl', 'start', 'md-next-backend'])
        self.phase('checking', 'Проверяем запуск панели и сохранность клиентов.')
        self.wait_ready()
        if self.saved_identities != identities(self.root):
            raise RuntimeError('Client identities changed')
        self.run(['systemctl', 'is-active', 'md-next-backend', 'xray', 'nginx'])
        (self.root / 'DEPLOYED_COMMIT').write_text(self.request['commit'] + '\n')
        (self.root / 'FRONTEND_DEPLOYED_COMMIT').write_text(self.request['commit'] + '\n')
        self.phase('success', 'Панель обновлена. Резервная копия сохранена; обновите страницу.')

    def wait_ready(self):
        for _ in range(90):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(1)
        raise RuntimeError('Panel health check failed')

    def rollback(self):
        self.phase('rolling_back', 'Новая панель не прошла проверку. Восстанавливаем предыдущую версию.')
        self.run(['systemctl', 'stop', 'md-next-backend'])
        with tarfile.open(self.backup / 'application.tar.gz') as archive:
            archive.extractall(self.root, filter='data')
        venv = self.root / 'backend/venv'
        if self.old_venv:
            if venv.is_symlink():
                venv.unlink()
            venv.symlink_to(self.old_venv, target_is_directory=True)
        for suffix in ('-wal', '-shm'):
            (self.root / ('backend/md_next.db' + suffix)).unlink(missing_ok=True)
        shutil.copy2(self.backup / 'md_next.db', self.root / 'backend/md_next.db')
        with tempfile.TemporaryDirectory(prefix='md-next-rollback-') as temporary:
            with tarfile.open(self.backup / 'vpn-configurations.tar.gz') as archive:
                archive.extractall(temporary, filter='data')
            for index, destination in enumerate(CONFIGS):
                source = Path(temporary) / str(index)
                if source.exists():
                    shutil.copy2(source, destination)
        self.run(['systemctl', 'restart', 'xray', 'md-next-backend', *self.awg_units])
        self.wait_ready()
        self.phase('rolled_back', 'Обновление не удалось. Предыдущая версия и данные восстановлены.')

    def execute(self):
        try:
            with tempfile.TemporaryDirectory(prefix='md-next-panel-update-') as temporary:
                repository = self.prepare(Path(temporary))
                self.save()
                self.activate(repository)
        except Exception:
            if self.activated:
                try:
                    self.rollback()
                except Exception:
                    self.phase('rollback_failed', 'Автоматический откат не завершён. Сохранена копия; требуется восстановление администратором сервера.')
                    raise
            else:
                self.phase('error', 'Подготовка обновления не удалась. Установленная панель и данные не изменены.')
            return False
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True, type=Path)
    request_path = parser.parse_args().request
    if os.geteuid() != 0 or request_path.parent != STATE or request_path.is_symlink():
        raise RuntimeError('Only a root-owned server update request is allowed')
    metadata = request_path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o077:
        raise RuntimeError('Unsafe request permissions')
    request = json.loads(request_path.read_text())
    request_path.unlink()
    if not re.fullmatch('[0-9a-f]{40}', request.get('commit', '')) or not Path(request.get('python', '')).is_absolute():
        raise RuntimeError('Invalid request')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        updater = Updater(ROOT, STATE, request)
        def terminate(signum, frame):
            raise RuntimeError('Update interrupted')
        signal.signal(signal.SIGTERM, terminate)
        log_path = STATE / 'update.log'
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as log:
            updater.log = log
            return 0 if updater.execute() else 1


if __name__ == '__main__':
    raise SystemExit(main())
