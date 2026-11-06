"""Чистые планы исправлений переносимых данных; без запуска служб."""
import configparser
from contextlib import closing
import ipaddress
import os
from pathlib import Path
import re
import sqlite3
import uuid


def certbot_link_plan(files):
    result = {}
    for name, content in files.items():
        if not name.startswith('etc/letsencrypt/renewal/') or not name.endswith('.conf'):
            continue
        lineage = Path(name).stem
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', lineage):
            raise ValueError('Нестандартное имя сертификата Certbot')
        parser = configparser.RawConfigParser()
        try: parser.read_string('[lineage]\n' + content.decode('utf-8'))
        except configparser.Error as error: raise ValueError('Некорректная конфигурация Certbot') from error
        config = parser['lineage']
        live = 'etc/letsencrypt/live/' + lineage + '/'
        archive = 'etc/letsencrypt/archive/' + lineage + '/'
        parts = ('cert', 'chain', 'fullchain', 'privkey')
        if config.get('archive_dir') != '/' + archive.rstrip('/') or any(
            config.get(part) != '/' + live + part + '.pem' or live + part + '.pem' not in files
            for part in parts
        ):
            raise ValueError('Нестандартная структура сертификата Certbot')
        generations = []
        for path in files:
            match = re.fullmatch(re.escape(archive) + r'cert([1-9][0-9]*)\.pem', path)
            if match:
                generation = match[1]
                if all(archive + part + generation + '.pem' in files and
                       files[archive + part + generation + '.pem'] == files[live + part + '.pem']
                       for part in parts):
                    generations.append(int(generation))
        if not generations:
            raise ValueError('Архив Certbot не соответствует активному сертификату')
        generation = str(max(generations))
        result.update({live + part + '.pem': archive + part + generation + '.pem' for part in parts})
    return result


def certbot_executable_plan(files, manifest):
    prefix = 'etc/letsencrypt/renewal-hooks/'
    hooks = {name for name in files if re.fullmatch(re.escape(prefix)+r'(pre|deploy|post)/[^/]+',name)}
    if 'certbot_executables' not in manifest:
        known = prefix+'deploy/md-next-xray-certificate.sh'
        if hooks - {known}:
            raise ValueError('Для нестандартных старых hooks Certbot нужны исходные права')
        return hooks
    executables = manifest['certbot_executables']
    if not isinstance(executables,list) or len(executables)>512 or any(
        not isinstance(name,str) or name not in hooks for name in executables
    ):
        raise ValueError('Недопустимые исполняемые файлы Certbot')
    return set(executables)


def renewal_files(files):
    result = dict(files)
    for name, content in files.items():
        if not name.startswith('etc/letsencrypt/renewal/') or not name.endswith('.conf'):
            continue
        text = content.decode('utf-8')
        parser = configparser.RawConfigParser()
        try: parser.read_string('[lineage]\n'+text)
        except configparser.Error as error: raise ValueError('Некорректный renewal-конфиг') from error
        auth = parser.get('renewalparams','authenticator',fallback='')
        if auth not in {'webroot','standalone','nginx'}:
            raise ValueError('Плагин Certbot требует ручной подготовки')
        if auth == 'standalone':
            site = files.get('etc/nginx/sites-available/md-next.conf',b'').decode('utf-8')
            if not re.search(r'location\s+/\.well-known/acme-challenge/\s*\{\s*root\s+/var/www/html\s*;',site):
                raise ValueError('Нет проверенного webroot для продления')
            if any(parser.get('renewalparams',key,fallback='').strip() for key in ('pre_hook','post_hook','auth_hook','cleanup_hook')):
                raise ValueError('Пользовательские hooks требуют ручной подготовки')
            if any('webroot_map' in section for section in parser.sections()):
                raise ValueError('Смешанный webroot требует ручной подготовки')
            text = re.sub(r'(?m)^\s*webroot_path\s*=.*(?:\n|$)','',text)
            text = re.sub(r'(?m)^\s*authenticator\s*=\s*standalone\s*$',
                          'authenticator = webroot\nwebroot_path = /var/www/html,',text)
            result[name] = text.encode('utf-8')
        elif auth == 'webroot':
            root = parser.get('renewalparams','webroot_path',fallback='/var/www/html,').strip().rstrip(',').strip()
            if root != '/var/www/html':
                raise ValueError('Нестандартный webroot требует ручной подготовки')
    return result


def restore_certbot_links(root, plan):
    root = Path(root).absolute()
    for source, target in plan.items():
        path, archive = root / source, root / target
        if any(p.is_symlink() for p in [archive, *archive.parents, *path.parents]):
            raise ValueError('Ссылка в каталоге Certbot')
        if path.is_symlink():
            if path.resolve() != archive:
                raise ValueError('Неожиданная ссылка Certbot')
            continue
        if path.read_bytes() != archive.read_bytes():
            raise ValueError('Сертификат изменился при восстановлении')
        temporary = path.with_name('.certbot-' + uuid.uuid4().hex)
        try:
            temporary.symlink_to(os.path.relpath(archive, path.parent))
            os.replace(temporary, path)
            if os.name == "posix":
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(fd)
                finally: os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)


def rebind_dns(database, address):
    with closing(sqlite3.connect(database)) as db, db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return
        enabled = db.execute("SELECT value FROM settings WHERE key='adguard.enabled'").fetchone()
        active = bool(enabled and enabled[0] == 'true')
        existing = db.execute("SELECT 1 FROM settings WHERE key='adguard.bootstrap_ip'").fetchone()
        if not active and (not existing or not address):
            return
        if not address:
            raise ValueError('Для DNS подписок нужен адрес целевого VPS')
        value = str(ipaddress.ip_address(address))
        db.execute("INSERT INTO settings(key,value) VALUES('adguard.bootstrap_ip',?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (value,))
