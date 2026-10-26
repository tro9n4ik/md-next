"""Определение старой установки без маркера только по совпадению исходников."""
import asyncio
import hashlib
from pathlib import Path
import time

MAX_CANDIDATES = 12
_cache = {}


def runtime_path(name):
    parts = Path(name).parts
    if not parts or Path(name).is_absolute() or '..' in parts:
        return False
    if '__pycache__' in parts or name.endswith(('.pyc', '.pyo')):
        return False
    if name.startswith('backend/app/static/fake/'):
        return False
    return (name.startswith(('backend/app/', 'backend/alembic/', 'frontend/src/'))
            or name in ('backend/VERSION', 'backend/requirements.txt', 'frontend/package.json', 'frontend/package-lock.json')
            or name.startswith('scripts/') and name.endswith(('.py', '.sh')))


def local_tree(root):
    result = {}
    root = root.resolve()
    for folder in ('backend/app', 'backend/alembic', 'frontend/src', 'scripts'):
        base = root / folder
        if base.is_dir():
            for path in base.rglob('*'):
                name = path.relative_to(root).as_posix()
                if runtime_path(name) and path.is_file():
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        return {}
                    data = path.read_bytes()
                    result[name] = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    for name in ('backend/VERSION', 'backend/requirements.txt', 'frontend/package.json', 'frontend/package-lock.json'):
        path = root / name
        if path.is_file():
            if path.is_symlink():
                return {}
            data = path.read_bytes()
            result[name] = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    return result


async def identify_installation(client, repository, root, head, headers):
    try:
        local = await asyncio.to_thread(local_tree, root)
    except OSError:
        return ''
    # Для неполной установки совпадение нескольких файлов недостаточно.
    required = {'backend/app/main.py', 'backend/VERSION', 'backend/requirements.txt', 'frontend/src/App.tsx', 'frontend/package.json', 'frontend/package-lock.json', 'scripts/install.sh'}
    if not required.issubset(local):
        return ''
    key = (str(root.resolve()), head, tuple(sorted(local.items())))
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < 600:
        return cached[1]
    response = await client.get(f'https://api.github.com/repos/{repository}/commits', params={'sha': head, 'per_page': MAX_CANDIDATES}, headers=headers)
    response.raise_for_status()
    commits = response.json()
    if not isinstance(commits, list):
        return ''
    import re
    candidates = [head] + [item.get('sha', '') for item in commits if isinstance(item, dict)]
    found = ''
    for commit in list(dict.fromkeys(candidates))[:MAX_CANDIDATES]:
        if not re.fullmatch(r'[0-9a-f]{40}', commit):
            continue
        response = await client.get(f'https://api.github.com/repos/{repository}/git/trees/{commit}', params={'recursive': '1'}, headers=headers)
        response.raise_for_status()
        tree = response.json()
        if tree.get('truncated'):
            continue
        entries = [entry for entry in tree.get('tree', []) if runtime_path(entry.get('path', '')) and entry.get('type') != 'tree']
        if any(entry.get('type') != 'blob' or entry.get('mode') not in ('100644', '100755') for entry in entries):
            continue
        remote = {entry['path']: entry.get('sha') for entry in entries}
        if remote == local:
            found = commit
            break
    _cache.clear()
    _cache[key] = (time.monotonic(), found)
    return found
