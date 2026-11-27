#!/usr/bin/env python3
"""Установка зафиксированных npm-зависимостей с резервным HTTPS-источником."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

PRIMARY = 'https://registry.npmjs.org/'
MIRRORS = ('https://registry.npmmirror.com/',)
ATTEMPT_TIMEOUT = 120
NETWORK_MARKERS = (
    'etimedout', 'econnreset', 'econnrefused', 'enotfound', 'eai_again',
    'enetunreach', 'ehostunreach', 'err_socket_timeout', 'socket hang up',
    'network is unreachable', 'network timeout', 'e408', 'e429', 'e500',
    'e502', 'e503', 'e504',
)
STOP_MARKERS = (
    'eintegrity', 'eusage', 'eresolve', 'etarget', 'ebadengine', 'e401', 'e403', 'e404',
    'enospc', 'eacces', 'eperm', 'enoent', 'elifecycle', 'err! command failed',
    'error command failed', 'cert_has_expired', 'self_signed_cert',
    'unable_to_verify_leaf_signature', 'unable_to_get_issuer_cert',
)


def network_failure(output, timed_out=False):
    text = output.lower()
    if any(marker in text for marker in STOP_MARKERS):
        return False
    return timed_out or any(marker in text for marker in NETWORK_MARKERS)


def safe_output(output):
    output = re.sub(r'(https?://)[^/\s@]+@', r'\1[скрыто]@', output)
    output = re.sub(r'(?i)((?:_authToken|authorization|password)\s*[:=]\s*)\S+', r'\1[скрыто]', output)
    return output


def run_attempt(command, directory, env, timeout):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait(timeout=5)
            code = 124
        log.seek(0, os.SEEK_END)
        size = log.tell()
        log.seek(max(0, size - 128 * 1024))
        return code, log.read().decode('utf-8', errors='replace'), timed_out


def configured_registry(directory, env):
    result = subprocess.run(['npm', 'config', 'get', 'registry'], cwd=directory, env=env,
                            capture_output=True, text=True, check=True, timeout=15)
    registry = result.stdout.strip().rstrip('/') + '/'
    url = urlsplit(registry)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Для загрузки npm требуется HTTPS-репозиторий без логина и пароля в URL.')
    return registry


def public_lock(directory):
    """Частные и нестандартные источники не перенаправляем на публичное зеркало."""
    lock = directory / 'package-lock.json'
    data = json.loads(lock.read_text(encoding='utf-8'))
    packages = data.get('packages')
    if not isinstance(packages, dict) or not packages:
        return False
    for name, package in packages.items():
        if not name:
            continue
        resolved = package.get('resolved', '')
        if package.get('inBundle') and not resolved:
            # Эти файлы входят в архив родительского пакета и защищены его
            # integrity. Отдельной загрузки и собственной суммы у них нет.
            parent = name
            while '/node_modules/' in parent:
                parent = parent.rsplit('/node_modules/', 1)[0]
                ancestor = packages.get(parent, {})
                if ancestor.get('resolved') and ancestor.get('integrity'):
                    break
            else:
                return False
            # Сам родитель также проверяется обычным проходом по lock-файлу.
            continue
        url = urlsplit(resolved)
        if (url.scheme != 'https' or url.hostname != 'registry.npmjs.org'
                or url.username or url.password or url.query or url.fragment or not package.get('integrity')):
            return False
    return True


def install(directory, env=None, runner=None):
    directory = Path(directory).resolve()
    env = dict(os.environ if env is None else env)
    runner = runner or run_attempt
    if not (directory / 'package-lock.json').is_file():
        raise ValueError('Не найден package-lock.json. Установка без зафиксированных версий запрещена.')
    registry = configured_registry(directory, env)
    registries = (registry,)
    if registry == PRIMARY and public_lock(directory):
        registries += MIRRORS
    for number, source in enumerate(registries):
        print(('Основной' if number == 0 else 'Резервный') + ' источник npm: ' + source, flush=True)
        command = ['npm', 'ci', '--no-audit', '--no-fund', '--strict-ssl=true',
                   '--replace-registry-host=npmjs', '--fetch-retries=1',
                   '--fetch-retry-mintimeout=1000', '--fetch-retry-maxtimeout=3000',
                   '--fetch-timeout=30000', '--maxsockets=4', '--registry=' + source]
        code, output, timed_out = runner(command, directory, env, ATTEMPT_TIMEOUT)
        print(safe_output(output), end='' if output.endswith('\n') else '\n', flush=True)
        if code == 0:
            print('Зависимости интерфейса установлены.', flush=True)
            return
        if not network_failure(output, timed_out):
            raise RuntimeError('Установка npm остановлена: ошибка пакетов, проверки целостности, сертификата или локальной системы. Источник не менялся.')
        if number + 1 < len(registries):
            print('Сетевая ошибка npm. Пробуем резервный источник с тем же lock-файлом.', flush=True)
    raise RuntimeError('NPM_NETWORK: Не удалось загрузить npm-зависимости ни из одного доступного источника.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    arguments = parser.parse_args()
    try:
        install(arguments.directory)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print('ОШИБКА: ' + safe_output(str(error)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
