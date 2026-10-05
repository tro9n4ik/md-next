"""Encrypted logical backups. Restore selected application tables transactionally."""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b'MDNEXT1\n'
TABLES = ('nodes', 'clients', 'client_profiles', 'routing_rules', 'settings')
BACKUP_DIR = Path(os.getenv('MD_BACKUP_DIR', 'backups'))
MAX_BYTES = 20 * 1024 * 1024


def instance_id():
    return hashlib.sha256(os.environ['JWT_SECRET_KEY'].encode()).hexdigest()


def cipher(password, salt):
    if len(password) < 12 or len(password) > 256:
        raise ValueError('Пароль копии должен содержать от 12 до 256 символов')
    key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def export_data():
    source = sqlite3.connect('md_next.db')
    snapshot = sqlite3.connect(':memory:')
    try:
        source.backup(snapshot)
        snapshot.row_factory = sqlite3.Row
        revision = snapshot.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        data = {table:[dict(row) for row in snapshot.execute(f'SELECT * FROM {table}')] for table in TABLES}
        # Binding/notification keys must not be revived after restoring an old snapshot.
        data['settings'] = [r for r in data['settings'] if not r['key'].startswith(('notify.', 'telegram.invite.'))]
        return {'format':1,'revision':revision,'instance':instance_id(), 'created_at':datetime.now(timezone.utc).isoformat(), 'tables':data}
    finally:
        source.close(); snapshot.close()


def create_backup(password):
    data = export_data()
    salt = secrets.token_bytes(16)
    blob = MAGIC + salt + cipher(password,salt).encrypt(json.dumps(data,ensure_ascii=False).encode())
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4) + '.mdbackup'
    path = BACKUP_DIR/name
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,'wb') as f: f.write(blob)
    meta = {'name':name, 'created_at':data['created_at'], 'size':len(blob), 'counts':{t:len(v) for t,v in data['tables'].items()}}
    path.with_suffix('.json').write_text(json.dumps(meta),encoding='utf-8')
    return meta


def read_backup(blob,password):
    if len(blob)>MAX_BYTES or not blob.startswith(MAGIC):
        raise ValueError('Неверный формат или размер копии (максимум 20 МБ)')
    try:
        plain = cipher(password,blob[len(MAGIC):len(MAGIC)+16]).decrypt(blob[len(MAGIC)+16:])
        data = json.loads(plain)
    except (InvalidToken, ValueError, TypeError):
        raise ValueError('Пароль неверен или копия повреждена') from None
    if data.get('format')!=1 or data.get('instance')!=instance_id():
        raise ValueError('Копия предназначена для другой установки панели')
    if set(data.get('tables',{}))!=set(TABLES):
        raise ValueError('Неполный состав копии')
    # Validate against the current schema before any mutation.
    with sqlite3.connect('md_next.db') as db:
        revision = db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        if data.get('revision')!=revision:
            raise ValueError('Версия схемы копии не совпадает с текущей')
        for table,rows in data['tables'].items():
            columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            if not isinstance(rows,list) or any(not isinstance(r,dict) or set(r)!=columns for r in rows):
                raise ValueError('Структура копии повреждена')
    return data


def list_backups():
    result=[]
    for path in BACKUP_DIR.glob('*.json'):
        try:
            meta=json.loads(path.read_text(encoding='utf-8'))
            if (BACKUP_DIR/meta['name']).is_file(): result.append(meta)
        except (ValueError,KeyError,OSError): pass
    return sorted(result,key=lambda r:r['created_at'],reverse=True)
