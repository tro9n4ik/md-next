"""Public updater diagnostics: fixed messages, never raw logs or credentials."""
import errno
import math
import os
from pathlib import Path
import subprocess
import time
from urllib.error import URLError

STAGES = {
    'download': 'Загрузка сборки', 'python_dependencies': 'Установка зависимостей Python',
    'frontend_dependencies': 'Установка зависимостей интерфейса', 'frontend_build': 'Сборка интерфейса',
    'backup': 'Резервная копия', 'activation': 'Применение файлов',
    'migration': 'Миграция базы данных', 'health': 'Проверка запуска панели',
}
MESSAGES = {
    'memory': ('Недостаточно оперативной памяти', 'Система завершила процесс обновления из-за нехватки RAM (OOM-kill). Проверьте доступную память и swap; при необходимости увеличьте их перед повторной попыткой.'),
    'disk': ('Недостаточно места на диске', 'Освободите место для загрузки, сборки и резервной копии. Не удаляйте базу, конфигурации и последнюю рабочую копию.'),
    'timeout': ('Превышено время ожидания', 'Операция не завершилась за отведённое время. Проверьте загрузку VPS и доступность сети перед повторным запуском.'),
    'network': ('Не удалось загрузить файлы', 'Проверьте доступность GitHub и репозиториев пакетов, DNS и TLS-сертификаты. Проверку сертификатов отключать не нужно.'),
    'dependencies': ('Не удалось установить зависимости', 'Проверьте доступность репозиториев пакетов и совместимость Python/Node.js. Точная ошибка сохранена в серверном журнале обновления.'),
    'build': ('Не удалось собрать интерфейс', 'Сборка завершилась с ошибкой. Проверьте журнал обновления на сервере. Нехватка памяти без подтверждения OOM-kill не предполагается.'),
    'migration': ('Не удалось обновить базу данных', 'Проверьте результат автоматического отката и резервную копию. Не запускайте миграции вручную до проверки журнала.'),
    'health': ('Новая панель не прошла проверку запуска', 'Проверьте результат отката и журнал службы md-next-backend. Сохраните резервную копию до восстановления работы.'),
    'backup': ('Не удалось создать резервную копию', 'Проверьте свободное место и доступность базы и конфигураций. Без резервной копии обновление не применяется.'),
    'unknown': ('Причину сбоя определить не удалось', 'Сохраните журнал /var/lib/md-next/updates/update.log для проверки администратором. Перед публикацией удалите секреты.'),
}
BUSY = {'queued', 'preparing', 'backup', 'installing', 'checking', 'rolling_back'}
FAILED = {'error', 'rolled_back', 'rollback_failed'}


def classify_failure(stage='', log='', *, oom=False, error=None):
    text = log.lower()
    if oom:
        code = 'memory'
    elif isinstance(error, OSError) and error.errno == errno.ENOSPC or 'no space left on device' in text or 'enospc' in text:
        code = 'disk'
    elif isinstance(error, subprocess.TimeoutExpired):
        code = 'timeout'
    elif isinstance(error, URLError) or any(marker in text for marker in ('could not resolve host', 'temporary failure in name resolution', 'certificate verify failed', 'unable to get local issuer certificate', 'network is unreachable', 'failed to connect to', 'npm_network', 'etimedout', 'econnreset', 'eai_again', 'enotfound')):
        code = 'network'
    elif stage in {'python_dependencies', 'frontend_dependencies'}:
        code = 'dependencies'
    elif stage == 'frontend_build':
        code = 'build'
    elif stage in {'migration', 'health', 'backup'}:
        code = stage
    else:
        code = 'unknown'
    title, hint = MESSAGES[code]
    return {'code': code, 'title': title, 'hint': hint, 'stage': STAGES.get(stage, 'Этап не определён')}


def log_tail(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(0, source.tell()-65536))
            return source.read(65536).decode('utf-8', errors='replace')
    except OSError:
        return ''


def system_output(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return result.stdout if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def enrich_failure(state, log_path=Path('/var/lib/md-next/updates/update.log'), *, error=None):
    state = dict(state)
    phase = state.get('phase')
    if phase not in FAILED | BUSY:
        return state
    started = state.get('started_at')
    valid_start = isinstance(started, (int, float)) and math.isfinite(started) and started > 0
    if phase in BUSY:
        if not valid_start or time.time()-started < 60:
            return state
        unit = system_output(['systemctl', 'show', 'md-next-panel-update.service', '--property=ActiveState'])
        if not any('ActiveState='+value in unit for value in ('inactive', 'failed')):
            return state
        state.update(phase='error', message='Процесс обновления прерван. Проверьте состояние панели перед повторным запуском.')
    journal = ''
    if valid_start:
        finished = state.get('finished_at', time.time())
        if not isinstance(finished, (int, float)) or not math.isfinite(finished):
            finished = time.time()
        journal = system_output(['journalctl', '-u', 'md-next-panel-update.service', '--since', '@'+str(int(started)),
                                 '--until', '@'+str(int(finished)+5), '--no-pager', '-o', 'cat', '-n', '100'])
    # A SIGKILL or Node's name alone is not proof of a kernel OOM kill.
    oom = 'oom-kill' in journal.lower() or 'killed by the oom killer' in journal.lower()
    log = log_tail(log_path)
    stage = state.get('stage', '')
    if not stage and ('tsc -b' in log or 'vite build' in log):
        stage = 'frontend_build'
    stored = state.get('failure', {})
    if not oom and error is None and stored.get('code') in MESSAGES:
        code = stored['code']
        title, hint = MESSAGES[code]
        state['failure'] = {'code': code, 'title': title, 'hint': hint, 'stage': STAGES.get(stage, 'Этап не определён')}
    else:
        state['failure'] = classify_failure(stage, log, oom=oom, error=error)
    return state
