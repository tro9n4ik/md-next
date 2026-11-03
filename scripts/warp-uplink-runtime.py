#!/usr/bin/python3 -I
"""Ограниченная подготовка локального WARP для канала к нодам."""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import uuid

LIB = Path('/usr/local/lib/md-next')
STATE = Path('/var/lib/md-next/warp-uplink')
BACKUPS = Path('/var/backups/md-next')
XRAY = Path('/usr/local/etc/xray/config.json')
PYTHON = '/opt/md-next/backend/venv/bin/python'
UNIT = 'md-next-uplink-prepare.service'
PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
PREPARING = {'queued', 'installing', 'registering', 'connecting'}
KEY = Path('/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg')
SOURCE = Path('/etc/apt/sources.list.d/md-next-cloudflare-warp.list')
OS_RELEASE = Path('/etc/os-release')
REGISTRATION_DIR = Path('/var/lib/cloudflare-warp')


def command(args, *, timeout=30, required=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={'PATH': PATH, 'LC_ALL': 'C', 'DEBIAN_FRONTEND': 'noninteractive'})
    if required and result.returncode:
        raise RuntimeError('Не удалось выполнить системный шаг подготовки WARP')
    return result


def atomic(path, data, mode=0o600):
    if path.is_symlink():
        raise ValueError('Недопустимая ссылка в настройках WARP')
    fd, name = tempfile.mkstemp(prefix='.warp-uplink-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def ensure_state():
    if STATE.is_symlink():
        raise ValueError('Недопустимое хранилище подготовки WARP')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Небезопасные права хранилища WARP')


def read_status():
    path = STATE/'status.json'
    return json.loads(path.read_text()) if path.exists() else {'phase': 'idle', 'message': 'Подключение ещё не подготовлено'}


def status():
    data = read_status()
    if data.get('phase') in PREPARING:
        active = command(['/usr/bin/systemctl', 'is-active', UNIT], timeout=5, required=False).stdout.strip()
        if active not in {'active', 'activating', 'deactivating', 'reloading'}:
            data = {**data, 'phase': 'failed', 'message': 'Предыдущая подготовка WARP прервалась. Повторите попытку.'}
    fields = {'phase', 'message', 'started_at', 'finished_at'}
    return {**{key: value for key, value in data.items() if key in fields},
            'installed': bool(shutil.which('warp-cli', path=PATH))}


def update(phase, message, **fields):
    ensure_state()
    previous = read_status()
    data = {'phase': phase, 'message': message, 'started_at': previous.get('started_at', time.time()), **fields}
    if phase in {'ready', 'failed'}:
        data['finished_at'] = time.time()
    atomic(STATE/'status.json', json.dumps(data, ensure_ascii=False).encode())


def validate(node_id, port):
    if type(node_id) is not int or not 1 <= node_id <= 2**63-1 or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Некорректные параметры подготовки WARP')
    config = json.loads(XRAY.read_text())
    node = next((item for item in config.get('outbounds', []) if item.get('tag') == f'node-{node_id}'), None)
    if not node or node.get('protocol') not in {'vless', 'trojan'}:
        raise ValueError('Выбранная нода отсутствует в рабочей конфигурации')


def start(node_id, port):
    import fcntl
    ensure_state()
    validate(node_id, port)
    with (STATE/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active = command(['/usr/bin/systemctl', 'is-active', UNIT], required=False).stdout.strip()
        if active in {'active', 'activating', 'deactivating', 'reloading'}:
            return status()
        (STATE/'recovery.json').unlink(missing_ok=True)
        request = STATE/('request-'+uuid.uuid4().hex+'.json')
        atomic(request, json.dumps({'node_id': node_id, 'port': port}).encode())
        update('queued', 'Подготовка локального WARP запланирована', started_at=time.time())
        try:
            command(['/usr/bin/systemd-run', '--unit='+UNIT, '--collect', '--property=RuntimeMaxSec=900',
                     '--property=TimeoutStopSec=45', '--property=KillMode=control-group',
                     '--property=ExecStopPost=/usr/bin/python3 -I '+str(LIB/'warp-uplink-runtime.py')+' --finish',
                     '--', '/usr/bin/python3', '-I', str(LIB/'warp-uplink-runtime.py'), '--request', str(request)])
        except Exception:
            request.unlink(missing_ok=True)
            update('failed', 'Не удалось запустить подготовку WARP')
            raise
        return status()


def finish():
    if read_status().get('phase') in PREPARING:
        recovery = STATE/'recovery.json'
        restored = restore_local(json.loads(recovery.read_text())) if recovery.exists() else True
        note = '' if restored else ' Проверьте параметры локальной службы WARP.'
        update('failed', 'Подготовка WARP прервалась. Повторите попытку; рабочий маршрут не изменён.'+note)
        if restored:
            recovery.unlink(missing_ok=True)


def restore_local(recovery):
    restored = True
    previous = recovery.get('previous')
    if recovery.get('configured') and previous:
        try:
            if previous['mode'] not in {'proxy', 'warp', 'warp+doh', 'doh', 'dot', 'warp+dot'}:
                raise ValueError('Неизвестный исходный режим WARP')
            cli('disconnect')
            cli('mode', previous['mode'])
            if previous.get('port'):
                cli('proxy', 'port', str(previous['port']))
        except Exception:
            restored = False
    if not recovery.get('active'):
        try:
            stopped = command(['/usr/bin/systemctl', 'stop', 'warp-svc'], required=False)
            restored = restored and stopped.returncode == 0
        except Exception:
            restored = False
    return restored


def parse_settings(text):
    mode = re.search(r'\bmode\s*:\s*(\S+)', text, re.I)
    port = re.search(r'(?:proxy(?:\s+listener)?\s+port|warpproxy\s+on\s+port)\s*:?\s*(\d+)', text, re.I)
    raw = mode.group(1).lower() if mode else ''
    modes = {'proxy': 'proxy', 'warpproxy': 'proxy', 'warp': 'warp', 'warponly': 'warp',
             'warp+doh': 'warp+doh', 'warpwithdns': 'warp+doh', 'warpwithdnsoverhttps': 'warp+doh',
             'doh': 'doh', 'dnsoverhttps': 'doh', 'dot': 'dot', 'dnsovertls': 'dot',
             'warp+dot': 'warp+dot', 'warpwithdnsovertls': 'warp+dot'}
    return {'mode': modes.get(raw, raw),
            'port': int(port.group(1)) if port else None}


def cli(*args, required=True):
    binary = shutil.which('warp-cli', path=PATH)
    if not binary:
        raise RuntimeError('Клиент Cloudflare WARP не установлен')
    return command([binary, '--accept-tos', *args], timeout=15, required=required)


def trace(port):
    result = command(['/usr/bin/curl', '--noproxy', '', '--socks5-hostname', f'127.0.0.1:{port}',
                      '--max-time', '8', '--fail', '--silent', '--show-error', 'https://1.1.1.1/cdn-cgi/trace'], timeout=12)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('warp') not in {'on', 'plus'} or ipaddress.ip_address(values.get('ip', '')).version != 4:
        raise RuntimeError('Подключение WARP не подтвердилось')


def download_repository_key():
    request = urllib.request.Request('https://pkg.cloudflareclient.com/pubkey.gpg',
                                     headers={'User-Agent': 'MD-Next-WARP/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            key_data = response.read(1024*1024+1)
    except urllib.error.HTTPError as error:
        raise ValueError(f'Не удалось загрузить ключ репозитория WARP: HTTP {error.code}') from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('Не удалось загрузить ключ репозитория WARP: проверьте доступность сети') from None
    if len(key_data) > 1024*1024:
        raise ValueError('Некорректный ключ репозитория WARP')
    return key_data


def install_package(backup):
    if shutil.which('warp-cli', path=PATH):
        return
    release = {}
    for line in OS_RELEASE.read_text().splitlines():
        key, _, value = line.partition('=')
        release[key] = value.strip('"')
    allowed = {'ubuntu': {'jammy', 'noble', 'resolute'}, 'debian': {'bookworm', 'trixie'}}
    codename = release.get('VERSION_CODENAME')
    if codename not in allowed.get(release.get('ID'), set()):
        raise ValueError('Автоматическая установка поддерживает Ubuntu 22.04, 24.04, 26.04 и Debian 12–13')
    saved = {}
    for path in (KEY, SOURCE):
        if path.is_symlink():
            raise ValueError('Настройки репозитория WARP содержат ссылку; автоматическая замена остановлена')
        data = path.read_bytes() if path.exists() else None
        saved[path] = data
        if data is not None:
            atomic(backup/path.name, data)
    try:
        update('installing', 'Устанавливаем Cloudflare WARP из официального репозитория')
        apt = ['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=60']
        command([*apt, 'update', '-qq'], timeout=180)
        command([*apt, 'install', '-y', '--no-install-recommends', 'ca-certificates', 'gnupg', 'curl'], timeout=180)
        key_data = download_repository_key()
        key = subprocess.run(['/usr/bin/gpg', '--dearmor'], input=key_data,
                             capture_output=True, timeout=15, check=True).stdout
        atomic(KEY, key, 0o644)
        atomic(SOURCE, f'deb [signed-by={KEY}] https://pkg.cloudflareclient.com/ {codename} main\n'.encode(), 0o644)
        command([*apt, 'update', '-qq'], timeout=180)
        command([*apt, 'install', '-y', '--no-install-recommends', 'cloudflare-warp'], timeout=240)
    except Exception:
        for path, data in saved.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, data, 0o644)
        raise


def prepare(node_id, port):
    validate(node_id, port)
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUPS/('uplink-setup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    backup.mkdir(mode=0o700)
    active = command(['/usr/bin/systemctl', 'is-active', 'warp-svc'], required=False).stdout.strip() == 'active'
    previous = None
    recovery = {'active': active, 'previous': None, 'configured': False}
    atomic(STATE/'recovery.json', json.dumps(recovery).encode())
    try:
        if shutil.which('warp-cli', path=PATH) and not active:
            directory = REGISTRATION_DIR
            if directory.exists() and any(directory.iterdir()):
                raise ValueError('На сервере есть выключенная настройка WARP. Сначала проверьте её службу; существующие настройки сохранены.')
        install_package(backup)
        command(['/usr/bin/systemctl', 'start', 'warp-svc'])
        for attempt in range(15):
            settings_result = cli('settings', required=False)
            if settings_result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Служба WARP не отвечает')
        previous = parse_settings(settings_result.stdout)
        previous['connected'] = bool(re.search(r'\bstatus(?:\s+update)?\s*:\s*connected\b', cli('status', required=False).stdout, re.I))
        atomic(backup/'settings-before.txt', settings_result.stdout.encode())
        atomic(backup/'state-before.json', json.dumps({'active': active, **previous}).encode())
        recovery['previous'] = previous
        atomic(STATE/'recovery.json', json.dumps(recovery).encode())
        if previous['mode'] not in {'proxy', 'warp', 'warp+doh', 'doh', 'dot', 'warp+dot'}:
            raise ValueError('Не удалось определить текущий режим WARP. Существующие настройки сохранены.')
        if previous['connected'] and (previous['mode'] != 'proxy' or previous['port'] != port):
            raise ValueError('На сервере уже используется другой режим или порт WARP. Автоматическая перенастройка остановлена.')
        if not previous['connected']:
            recovery['configured'] = True
            atomic(STATE/'recovery.json', json.dumps(recovery).encode())
            cli('mode', 'proxy')
            cli('proxy', 'port', str(port))
        registration = cli('registration', 'show', required=False)
        if registration.returncode:
            if not re.search(r'missing|not\s+(?:found|registered)|does not exist|registration\s+required', registration.stdout+registration.stderr, re.I):
                raise RuntimeError('Не удалось проверить регистрацию WARP. Существующая учётная запись сохранена.')
            update('registering', 'Регистрируем бесплатный WARP через выбранную ноду')
            helper = str(LIB/'warp_registration.py')
            command(['/usr/bin/systemd-run', '--quiet', '--wait', '--collect', '--pipe', '--unit=md-next-warp-registration',
                     '--property=RuntimeMaxSec=60', '--property=TimeoutStopSec=30', '--property=KillMode=control-group',
                     f'--property=ExecStopPost={PYTHON} {helper} --cleanup',
                     PYTHON, helper, '--node-id', str(node_id), '--config', str(XRAY)], timeout=100)
        update('connecting', 'Подключаем локальный прокси WARP и проверяем выход')
        if not previous['connected']:
            cli('connect')
        for attempt in range(8):
            try:
                trace(port)
                break
            except (OSError, RuntimeError, ValueError):
                if attempt == 7:
                    raise
                time.sleep(2)
        command(['/usr/bin/systemctl', 'enable', 'warp-svc'])
        update('ready', 'Локальный WARP готов. Включите канал к нодам переключателем в панели.')
        (STATE/'recovery.json').unlink(missing_ok=True)
    except Exception as exc:
        restored = restore_local(recovery)
        if restored:
            (STATE/'recovery.json').unlink(missing_ok=True)
        message = str(exc) if isinstance(exc, ValueError) else 'Подготовка WARP не завершилась. Повторите попытку или проверьте журнал службы подготовки.'
        update('failed', message+' Рабочий маршрут панели не изменён.'+('' if restored else ' Проверьте параметры локальной службы WARP.'))
        raise


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Подготовка WARP требует системного помощника')
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path)
    parser.add_argument('--finish', action='store_true')
    args = parser.parse_args()
    if args.finish:
        finish()
        return
    path = args.request
    if not path or path.parent != STATE or not re.fullmatch(r'request-[0-9a-f]{32}\.json', path.name) or path.is_symlink():
        raise ValueError('Недопустимый запрос подготовки WARP')
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o077 or metadata.st_size > 1024:
        raise ValueError('Небезопасный запрос подготовки WARP')
    data = json.loads(path.read_text())
    path.unlink()
    prepare(data['node_id'], data['port'])


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Подготовка WARP не завершена; подробности в состоянии панели.', flush=True)
        raise SystemExit(1)
