"""Authenticated, pinned first-party updates dispatched outside the API process."""
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import httpx

from app.services.shell import run_cmd

APP_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = Path('/var/lib/md-next/updates')
REPOSITORY = 'tro9n4ik/md-next'
_launch_lock = asyncio.Lock()
BUSY = {'queued', 'preparing', 'backup', 'installing', 'checking', 'rolling_back'}


def read_state():
    try:
        state = json.loads((STATE_DIR / 'status.json').read_text(encoding='utf-8'))
        return {key: state[key] for key in ('phase', 'message', 'commit', 'backup', 'started_at', 'finished_at') if key in state}
    except (OSError, ValueError):
        return {'phase': 'idle', 'message': 'Обновление ещё не запускалось.'}


def write_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = STATE_DIR / 'status.next'
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(STATE_DIR / 'status.json')


def installed_info():
    def read(name):
        try:
            return (APP_ROOT / name).read_text(encoding='utf-8').strip()
        except OSError:
            return ''
    return {'version': read('backend/VERSION'), 'commit': read('DEPLOYED_COMMIT')}


async def get_status():
    state = read_state()
    if state.get('phase') in BUSY:
        try:
            code, active, _ = await run_cmd('systemctl', 'is-active', 'md-next-panel-update.service', timeout=5)
            if code and active.strip() not in ('active', 'activating') and time.time() - state.get('started_at', 0) > 60:
                state = {**state, 'phase': 'error', 'message': 'Процесс обновления прерван. Проверьте состояние панели и сохранённую копию перед повторным запуском.'}
        except RuntimeError:
            pass
    return {'installed': installed_info(), 'job': state, 'can_install': sys.platform.startswith('linux') and APP_ROOT == Path('/opt/md-next') and (APP_ROOT / 'scripts/update-panel.py').is_file()}


async def check_update(token=''):
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'MD-Next-updater'}
    token = token or os.getenv('MDNEXT_GITHUB_TOKEN', '')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(f'https://api.github.com/repos/{REPOSITORY}/commits/main', headers=headers)
        if response.status_code in (401, 403, 404):
            raise ValueError('Нет доступа к репозиторию GitHub. Для приватного проекта укажите токен с правом чтения репозитория.')
        response.raise_for_status()
        data = response.json()
        commit = data.get('sha', '')
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError('GitHub вернул некорректный идентификатор версии.')
        verified = data.get('commit', {}).get('verification', {}).get('verified') is True
        runs = await client.get(f'https://api.github.com/repos/{REPOSITORY}/actions/runs', params={'head_sha': commit, 'event': 'push', 'per_page': 30}, headers=headers)
        runs.raise_for_status()
        checks = [run for run in runs.json().get('workflow_runs', []) if run.get('path') in ('.github/workflows/ci.yml', '.github/workflows/secrets.yml')]
        # Only the newest attempt for each required workflow counts.
        latest = {}
        for run in checks:
            latest.setdefault(run['path'], run)
        ready = verified and all(latest.get(path, {}).get('conclusion') == 'success' for path in ('.github/workflows/ci.yml', '.github/workflows/secrets.yml'))
    installed = installed_info()
    current = installed['commit']
    return {'commit': commit, 'summary': data.get('commit', {}).get('message', '').split('\n')[0][:300], 'published_at': data.get('commit', {}).get('committer', {}).get('date'), 'available': not (len(current) >= 7 and commit.startswith(current)), 'ready': ready, 'signature_verified': verified, 'installed': installed}


async def start_update(commit, token=''):
    async with _launch_lock:
        status = await get_status()
        if not status['can_install']:
            raise ValueError('Обновление доступно только для установленной серверной панели.')
        if status['job'].get('phase') in BUSY:
            raise ValueError('Обновление уже выполняется.')
        candidate = await check_update(token)
        if candidate['commit'] != commit or not candidate['ready']:
            raise ValueError('Версия изменилась или ещё не прошла проверки. Проверьте обновления повторно.')
        if not candidate['available']:
            raise ValueError('Эта версия уже установлена.')
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        request_file = STATE_DIR / ('request-' + uuid.uuid4().hex + '.json')
        descriptor = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            json.dump({'commit': commit, 'token': token or os.getenv('MDNEXT_GITHUB_TOKEN', ''), 'python': os.path.realpath(sys._base_executable)}, target)
        state = {'phase': 'queued', 'message': 'Подготовка обновления.', 'commit': commit, 'started_at': time.time()}
        write_state(state)
        try:
            code, _, _ = await run_cmd('systemd-run', '--unit=md-next-panel-update', '--collect', '--property=RuntimeMaxSec=1800', '--', '/usr/bin/python3', str(APP_ROOT / 'scripts/update-panel.py'), '--request', str(request_file), timeout=15)
            if code:
                raise RuntimeError('Не удалось запустить процесс обновления.')
        except Exception:
            request_file.unlink(missing_ok=True)
            write_state({**state, 'phase': 'error', 'message': 'Не удалось запустить обновление. Панель не изменена.'})
            raise RuntimeError('Не удалось запустить обновление. Панель не изменена.') from None
        return state
