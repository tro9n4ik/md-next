"""Portable encrypted migration format. Never extracts caller-selected paths."""
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import importlib.util
import time
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path, PurePosixPath

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b'MDNEXT-MOVE-1\n'
MAX_BYTES = 256 * 1024 * 1024
SINGLE_FILES = {
    'opt/md-next/backend/.env', 'usr/local/etc/xray/config.json',
    'etc/nginx/nginx.conf', 'etc/nginx/sites-available/md-next.conf',
    'etc/nginx/stream-available/md-next-stream.conf',
    'var/lib/md-next-adguard/AdGuardHome.yaml',
    'etc/nginx/conf.d/md-next-security.conf', 'etc/nginx/md-next-panel-bans.map',
    'etc/systemd/system/md-next-backend.service', 'etc/systemd/system/xray.service',
    'etc/systemd/system/md-next-awg.service',
}
DIRECTORIES = (
    'usr/local/etc/xray/tls/', 'etc/amnezia/amneziawg/', 'etc/letsencrypt/',
    'etc/nginx/ssl_dummy/', 'etc/systemd/system/xray.service.d/',
    'var/lib/md-next-adguard/data/filters/',
    'opt/md-next/data/placeholder/', 'opt/md-next/backend/app/static/fake/',
)
REQUIRED = {'opt/md-next/backend/.env', 'usr/local/etc/xray/config.json',
            'etc/nginx/sites-available/md-next.conf', 'etc/nginx/stream-available/md-next-stream.conf'}


def validate_nginx_dependencies(files):
    site = files.get('etc/nginx/sites-available/md-next.conf', b'').decode('utf-8')
    security = 'etc/nginx/conf.d/md-next-security.conf'
    bans = 'etc/nginx/md-next-panel-bans.map'
    uses_security = re.search(r'\b(?:md_auth|md_login|md_public|md_scanner(?:_total)?|md_web_connections|md_panel_banned)\b', site)
    if uses_security and security not in files or security in files and bans not in files:
        raise ValueError('В комплекте отсутствуют настройки защиты Nginx или карта блокировок панели')


class InsufficientMemoryError(ValueError):
    code='MIG_MEMORY'


def guard_memory(required, root=Path('/')):
    if os.name=='posix' and Path(root)==Path('/') and Path('/proc/meminfo').is_file():
        match=re.search(r'(?m)^MemAvailable:\s+(\d+) kB',Path('/proc/meminfo').read_text())
        if not match or int(match.group(1))*1024<required:
            raise InsufficientMemoryError('Недостаточно свободной памяти для безопасной подготовки комплекта')


def allowed_path(name):
    p = PurePosixPath(name)
    return (isinstance(name, str) and '\\' not in name and '\0' not in name and
            not p.is_absolute() and not any(x in {'.', '..'} for x in name.split('/')) and
            str(p) == name and (name in SINGLE_FILES or name in {'opt/md-next-adguard/AdGuardHome','etc/systemd/system/md-next-adguard.service'} or any(name.startswith(d) for d in DIRECTORIES)))


def cipher(password, salt):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('Пароль комплекта должен содержать от 12 до 256 символов')
    key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encode_bundle(manifest, database, files, password):
    if set(files) - {p for p in files if allowed_path(p)}:
        raise ValueError('Недопустимый путь в комплекте')
    data = {'format': 1, 'manifest': manifest, 'database': base64.b64encode(database).decode(),
            'files': {p: {'data': base64.b64encode(blob).decode(), 'sha256': hashlib.sha256(blob).hexdigest()} for p, blob in files.items()},
            'database_sha256': hashlib.sha256(database).hexdigest()}
    raw = json.dumps(data, ensure_ascii=False).encode()
    if len(raw) > MAX_BYTES // 2:
        raise ValueError('Комплект превышает допустимый размер')
    salt = secrets.token_bytes(16)
    return MAGIC + salt + cipher(password, salt).encrypt(raw)


def decode_bundle(blob, password):
    if len(blob) > MAX_BYTES or not blob.startswith(MAGIC) or len(blob) < len(MAGIC)+16:
        raise ValueError('Неверный формат или размер комплекта')
    try:
        raw = cipher(password, blob[len(MAGIC):len(MAGIC)+16]).decrypt(blob[len(MAGIC)+16:])
        data = json.loads(raw)
        if data.get('format') != 1 or not isinstance(data.get('manifest'), dict): raise ValueError()
        database = base64.b64decode(data['database'], validate=True)
        if hashlib.sha256(database).hexdigest() != data['database_sha256']: raise ValueError()
        files = {}
        for name, entry in data['files'].items():
            if not allowed_path(name): raise ValueError()
            content = base64.b64decode(entry['data'], validate=True)
            if hashlib.sha256(content).hexdigest() != entry['sha256']: raise ValueError()
            files[name] = content
        if not REQUIRED <= files.keys(): raise ValueError()
        validate_nginx_dependencies(files)
        if not database.startswith(b'SQLite format 3\0'): raise ValueError()
        return data['manifest'], database, files
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('Пароль неверен, комплект повреждён или его состав недопустим') from None


def snapshot(source, target):
    deadline=time.monotonic()+60
    def progress(status,remaining,total):
        if time.monotonic()>deadline:raise TimeoutError('Истекло время согласованного снимка SQLite')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as live, closing(sqlite3.connect(target)) as saved:
        live.backup(saved,pages=256,progress=progress)
        if saved.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Не удалось проверить целостность снимка базы')


def read_confined(root, name):
    """Open every component without following app-writable directory symlinks."""
    if os.name != 'posix':
        path=root/name
        if any(p.is_symlink() for p in [path,*path.parents] if p.is_relative_to(root)):
            raise ValueError('Ссылка в пути файла')
        return path.read_bytes()
    descriptor=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:
        parts=PurePosixPath(name).parts
        for part in parts[:-1]:
            next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
            os.close(descriptor);descriptor=next_fd
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=descriptor)
        try:
            import stat
            metadata=os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size>16*1024*1024:raise ValueError('Неверный файл комплекта')
            with os.fdopen(fd,'rb',closefd=False) as stream:return stream.read(16*1024*1024+1)
        finally:os.close(fd)
    finally:os.close(descriptor)


def inventory_database(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('База повреждена')
        revision = db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        # Names originate from sqlite_master, still quote identifiers correctly.
        counts = {name: db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        return {'revision': revision, 'tables': counts}


def portable_secrets(database, environment):
    import io
    from dotenv import dotenv_values
    installation_secret = dotenv_values(stream=io.StringIO(environment), interpolate=False).get("JWT_SECRET_KEY")
    source = Path(__file__).with_name("node-crypto.py")
    if not source.is_file():
        source = Path(__file__).with_name("crypto.py")
    spec = importlib.util.spec_from_file_location("migration_crypto", source)
    crypto = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(crypto)
    with closing(sqlite3.connect(database)) as db, db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" in tables:
            for identifier, secret in db.execute("SELECT id, secret FROM nodes").fetchall():
                value = crypto.portable_node_secret(secret, installation_secret=installation_secret)
                db.execute("UPDATE nodes SET secret=? WHERE id=?", (value, identifier))


def capture(root, password, *, frozen=False, portable=False):
    import tempfile
    root = Path(root).resolve()
    def memory_guard(size):
        guard_memory(size*10+96*1024*1024,root)
    memory_guard(16*1024*1024)
    app = root/'opt/md-next'
    files = {}
    certbot_executables = []
    paths = [root/p for p in SINGLE_FILES]
    for directory in DIRECTORIES:
        parent = root/directory
        if parent.exists(): paths.extend(p for p in parent.rglob('*') if p.is_file())
    total = 0
    for path in paths:
        if not path.exists(): continue
        name = path.relative_to(root).as_posix()
        resolved = path.resolve()
        if not resolved.is_relative_to(root): raise ValueError('Файл вне корня комплекта')
        if path.is_symlink() and not name.startswith('etc/letsencrypt/'):
            raise ValueError('Неожиданная ссылка в конфигурациях')
        size = path.stat().st_size
        total += size
        if size > 16*1024*1024 or total > MAX_BYTES//4: raise ValueError('Слишком большой набор конфигураций')
        read_name=resolved.relative_to(root).as_posix() if name.startswith('etc/letsencrypt/') else name
        if not allowed_path(read_name):raise ValueError('Ссылка вне разрешённого каталога')
        files[name] = read_confined(root,read_name)
        if re.fullmatch(r'etc/letsencrypt/renewal-hooks/(pre|deploy|post)/[^/]+',name) and path.stat().st_mode & 0o111:
            certbot_executables.append(name)
    if not REQUIRED <= files.keys(): raise ValueError('Не найдены обязательные конфигурации панели')
    validate_nginx_dependencies(files)
    env = files['opt/md-next/backend/.env'].decode('utf-8')
    if not re.search(r'(?m)^JWT_SECRET_KEY=.+$', env): raise ValueError('В .env отсутствует ключ установки')
    referenced=[]
    def collect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'certificateFile','keyFile'}:referenced.append(item)
                collect(item)
        elif isinstance(value,list):
            for item in value:collect(item)
    collect(json.loads(files['usr/local/etc/xray/config.json']))
    for key in ('TLS_CERT_PATH','TLS_KEY_PATH'):
        match=re.search(r'(?m)^'+key+r'=(.+)$',env)
        if match:referenced.append(match.group(1).strip().strip('"').strip("'"))
    nginx=files['etc/nginx/sites-available/md-next.conf'].decode('utf-8')
    referenced.extend(re.findall(r'(?m)^\s*ssl_certificate(?:_key)?\s+([^;\s]+)\s*;',nginx))
    if any(not isinstance(p,str) or p.lstrip('/') not in files for p in referenced):
        raise ValueError('Не все сертификаты входят в разрешённый комплект. Нестандартные пути требуют ручной проверки.')
    db_source = app/'backend/md_next.db'
    # The installation intentionally links its database into /var/lib/md-next/data.
    if db_source.is_symlink() and db_source.resolve() != root/'var/lib/md-next/data/md_next.db':
        raise ValueError('Неожиданное расположение базы')
    memory_guard(total+db_source.stat().st_size)
    with tempfile.TemporaryDirectory(prefix='mdnext-move-') as temp:
        saved = Path(temp)/'snapshot.db'; snapshot(db_source.resolve(), saved)
        portable_secrets(saved, env)
        memory_guard(total+saved.stat().st_size)
        metadata = inventory_database(saved)
        components={}
        with closing(sqlite3.connect(saved)) as db:
            if 'settings' in metadata['tables']:
                enabled=db.execute("SELECT value FROM settings WHERE key='adguard.enabled'").fetchone()
                if enabled and enabled[0]=='true':
                    if 'var/lib/md-next-adguard/AdGuardHome.yaml' not in files:
                        raise ValueError('Отсутствует конфигурация AdGuard Home')
                    import subprocess
                    result=subprocess.run([str(root/'opt/md-next-adguard/AdGuardHome'),'--version'],capture_output=True,text=True,timeout=10,check=True)
                    match=re.search(r'version (v[0-9]+\.[0-9]+\.[0-9]+)',result.stdout+result.stderr)
                    if not match:raise ValueError('Не удалось определить версию AdGuard Home')
                    components['adguard']={'version':match[1]}

        manifest = {'certbot_executables':sorted(certbot_executables),'components':components, 'restore_mode': 'clean-install' if portable else 'snapshot',
                    'version': (app/'backend/VERSION').read_text().strip(),
                    'commit': (app/'DEPLOYED_COMMIT').read_text().strip(),
                    'created_at': datetime.now(timezone.utc).isoformat(), 'frozen': frozen,
                    'installation': hashlib.sha256(re.search(r'(?m)^JWT_SECRET_KEY=(.+)$', env).group(1).encode()).hexdigest(), **metadata}
        if not re.fullmatch('[0-9a-f]{40}', manifest['commit']): raise ValueError('Сборка установки не определена')
        return encode_bundle(manifest, saved.read_bytes(), files, password), manifest
