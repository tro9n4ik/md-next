"""Выбор пути XHTTP из БД; устаревшая .env используется только как fallback."""
import re
from contextlib import closing
import secrets
import sqlite3
import sys
from pathlib import Path


def resolve(database, fallback=''):
    value = ''
    path = Path(database)
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute("SELECT value FROM settings WHERE key='profiles.path.vless_xhttp_tls'").fetchone()
            value = row[0] if row and row[0] else ''
    value = value or fallback.strip().strip('\"\'') or '/'+secrets.token_hex(16)
    if not re.fullmatch(r'/[A-Za-z0-9/_-]{1,200}', value) or value.startswith('//'):
        raise ValueError('Некорректный путь XHTTP TLS; настройки не изменены')
    return value


if __name__ == '__main__':
    print(resolve(*sys.argv[1:]))
