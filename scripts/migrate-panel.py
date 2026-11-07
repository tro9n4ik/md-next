#!/usr/bin/env python3
"""Explicit root-only migration operations; DNS is never changed here."""
import argparse
try:
    import fcntl
except ImportError:
    fcntl = None
import getpass
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import hashlib
import time
import stat
import uuid
import re

spec=importlib.util.spec_from_file_location('migration_events',Path(__file__).with_name('migration-events.py'))
events=importlib.util.module_from_spec(spec);spec.loader.exec_module(events)

FILESYSTEM = Path('/')
ROOT = FILESYSTEM/'opt/md-next'
STATE = Path('/var/lib/md-next/migration')
SERVICES = ('md-next-backend', 'md-next-mtproto', 'xray', 'md-next-awg')


def library():
    installed = Path(__file__).with_name('migration-bundle.py')
    source = installed if installed.is_file() else ROOT/'backend/app/services/migration_bundle.py'
    spec = importlib.util.spec_from_file_location('migration_bundle', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def command(*args, check=True):
    return subprocess.run(list(args), check=check, capture_output=True, text=True, timeout=120)


def write(path, blob, mode=0o600):
    path = Path(path)
    if os.name == 'posix':
        path=path.absolute()
        directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        temporary='.migration-'+uuid.uuid4().hex
        try:
            for part in path.parts[1:-1]:
                try:os.mkdir(part,0o755,dir_fd=directory)
                except FileExistsError:pass
                next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=next_fd
            try:
                if stat.S_ISLNK(os.stat(path.name,dir_fd=directory,follow_symlinks=False).st_mode):raise RuntimeError('Ссылка в целевом пути')
            except FileNotFoundError:pass
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=directory)
            with os.fdopen(fd,'wb') as stream:
                stream.write(blob);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path.name,src_dir_fd=directory,dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:os.unlink(temporary,dir_fd=directory)
            except FileNotFoundError:pass
            os.close(directory)
        return
    # Never follow a symlink in a destination supplied by the package.
    if any(p.is_symlink() for p in [path, *path.parents]): raise RuntimeError('Ссылка в целевом пути')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.migration-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.chmod(name, mode)
            stream.write(blob)
            stream.flush();os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def staging_env(content, staging):
    lines = [line for line in content.decode().splitlines() if not line.startswith('MDNEXT_MIGRATION_STAGING=')]
    if staging: lines.append('MDNEXT_MIGRATION_STAGING=1')
    return ('\n'.join(lines)+'\n').encode()


def database_digest():
    """Include committed WAL pages; hashing only the main .db misses changes."""
    with tempfile.TemporaryDirectory(prefix='mdnext-verify-') as folder:
        saved=Path(folder)/'snapshot.db'
        library().snapshot((ROOT/'backend/md_next.db').resolve(),saved)
        digest=hashlib.sha256()
        with saved.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        return digest.hexdigest()


def configuration_digest():
    bundle=library();paths={FILESYSTEM/name for name in bundle.SINGLE_FILES}
    for directory in bundle.DIRECTORIES:
        folder=FILESYSTEM/directory
        if folder.exists():paths.update(path for path in folder.rglob('*') if path.is_file())
    paths.update(FILESYSTEM/name for name in ('etc/systemd/system/md-next-adguard.service','opt/md-next-adguard/AdGuardHome'))
    digest=hashlib.sha256()
    for path in sorted(paths):
        if path.is_file():
            digest.update(str(path.relative_to(FILESYSTEM)).encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def start_services():
    for service in reversed(SERVICES):
        command('systemctl','start',service)
    command('systemctl','restart','md-next-backend')


def drop_to_panel_user():
    """Проверка использует того же владельца черновиков, что и работающая панель."""
    import pwd
    account=pwd.getpwnam('md-next')
    if account.pw_uid==0:raise RuntimeError('Пользователь панели не должен быть root')
    os.initgroups(account.pw_name,account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.environ['HOME']=account.pw_dir


def nginx_tokens(text):
    lexer=re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\#[^\n]*|[{};]|(?:\$\{[^}]+\}|\{\d+(?:,\d*)?\}|\\.|[^\s{};\#"'])+''')
    return [(m.group(),m.start(),m.end()) for m in lexer.finditer(text) if not m.group().startswith('#')]


def uses_http2(text):
    words=[]
    for token,_,_ in nginx_tokens(text):
        if token in {"{","}"}:words=[]
        elif token==";":
            if words and (words[0]=="http2" or words[0]=="listen" and "http2" in words[1:]):return True
            words=[]
        else:words.append(token)
    return False


def normalize_nginx_http2(text, version):
    """Translate server-scoped HTTP/2 only, preserving ports, TLS and routing.

    Nginx 1.25.1 introduced `http2 on`; older builds require listen's http2
    parameter. Unknown/inherited layouts fail closed instead of disabling h2.
    """
    if version >= (1,25,1):return text
    tokens=nginx_tokens(text)
    if not any(token[0]=='http2' for token in tokens):return text
    edits=[];index=0
    def parse(nested=False):
        nonlocal index
        nodes=[]
        while index<len(tokens):
            if tokens[index][0]=='}':
                if not nested:raise events.MigrationError('MIG_NGINX_COMPAT')
                index+=1;return nodes
            head=[]
            while index<len(tokens) and tokens[index][0] not in {'{','}',';'}:
                head.append(tokens[index]);index+=1
            if not head or index>=len(tokens) or tokens[index][0]=='}':raise events.MigrationError('MIG_NGINX_COMPAT')
            ending=tokens[index];index+=1
            children=parse(True) if ending[0]=='{' else None
            nodes.append((head,ending,children))
        if nested:raise events.MigrationError('MIG_NGINX_COMPAT')
        return nodes
    def visit(nodes):
        for head,ending,children in nodes:
            if head[0][0]=='server' and children is not None:
                directives=[node for node in children if node[0][0][0]=='http2']
                if len(directives)>1:raise events.MigrationError('MIG_NGINX_COMPAT')
                if directives:
                    directive=directives[0];args=directive[0]
                    if len(args)!=2 or args[1][0] not in {'on','off'}:raise events.MigrationError('MIG_NGINX_COMPAT')
                    listens=[node for node in children if node[0][0][0]=='listen' and node[2] is None]
                    if not listens:raise events.MigrationError('MIG_NGINX_COMPAT')
                    for values,end,_ in listens:
                        has_h2=any(token[0]=='http2' for token in values[1:])
                        if args[1][0]=='off' and has_h2:raise events.MigrationError('MIG_NGINX_COMPAT')
                        if args[1][0]=='on' and not has_h2:edits.append((end[1],end[1],' http2'))
                    edits.append((args[0][1],directive[1][2],''))
                # Nested location blocks cannot contain a valid http2 switch.
                visit([node for node in children if node not in directives])
            elif head[0][0]=='http2':raise events.MigrationError('MIG_NGINX_COMPAT')
            elif children is not None:visit(children)
    visit(parse())
    for start,end,replacement in sorted(edits,reverse=True):text=text[:start]+replacement+text[end:]
    return text


def component_capabilities(files):
    try:result=command('nginx','-V',check=False)
    except (OSError,subprocess.SubprocessError) as error:raise events.MigrationError('MIG_COMPONENT_MISSING') from error
    build=getattr(result,'stdout','')+getattr(result,'stderr','')
    match=re.search(r'nginx/(\d+)\.(\d+)\.(\d+)',build)
    if result.returncode or not match:raise events.MigrationError('MIG_NGINX_VERSION')
    version=tuple(map(int,match.groups()))
    name='etc/nginx/sites-available/md-next.conf'
    original=files[name].decode('utf-8')
    if uses_http2(original) and '--with-http_v2_module' not in build:raise events.MigrationError('MIG_NGINX_MODULE')
    files={**files,name:normalize_nginx_http2(original,version).encode('utf-8')}
    for args in (('/usr/local/bin/xray','version'),('awg','--version')):
        try:result=command(*args,check=False)
        except (OSError,subprocess.SubprocessError) as error:raise events.MigrationError('MIG_COMPONENT_MISSING') from error
        if result.returncode:raise events.MigrationError('MIG_COMPONENT_MISSING')
    return files


def validate_components():
    for args,code in ((('nginx','-t'),'MIG_NGINX_CONFIG'),(('/usr/local/bin/xray','run','-test','-config','/usr/local/etc/xray/config.json'),'MIG_XRAY_CONFIG')):
        result=command(*args,check=False)
        if result.returncode:raise events.MigrationError(code,exit_code=result.returncode)


def has_panel_security():
    return (FILESYSTEM/'etc/nginx/conf.d/md-next-security.conf').is_file()


def prepare_fail2ban():
    if not has_panel_security():
        return
    packages = ('fail2ban', 'python3-systemd', 'nftables')
    installed = command('dpkg-query', '-W', '-f=${db:Status-Status}\\n', *packages, check=False)
    if installed.returncode == 0 and installed.stdout.splitlines() == ['installed'] * len(packages):
        return
    environment = {**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}
    try:
        subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y', *packages],
                       env=environment, check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        raise events.MigrationError('MIG_FAIL2BAN') from error


def clean_install_files(files):
    # Системные файлы принадлежат чистому билду, данные — исходной установке.
    result={name:data for name,data in files.items()
            if not name.startswith(('etc/systemd/','etc/nginx/')) or name.startswith('etc/nginx/ssl_dummy/')}
    name='etc/nginx/sites-available/md-next.conf'
    source=files[name].decode('utf-8')
    target=(FILESYSTEM/name).read_text()
    for directive in ('ssl_certificate','ssl_certificate_key'):
        paths=set(re.findall(r'(?m)^\s*'+directive+r'\s+([^;\s]+)\s*;',source))
        if len(paths)!=1:raise events.MigrationError('MIG_TLS_LAYOUT')
        path=paths.pop()
        if path.lstrip('/') not in files:raise events.MigrationError('MIG_BUNDLE')
        target=re.sub(r'(?m)^(\s*'+directive+r'\s+)[^;\s]+(\s*;)',lambda m:m[1]+path+m[2],target)
    if 'etc/nginx/conf.d/md-next-security.conf' in files:
        spec=importlib.util.spec_from_file_location('installed_security',ROOT/'scripts/install-security.py')
        security=importlib.util.module_from_spec(spec);spec.loader.exec_module(security)
        target=security.transform(target)
        security.configure_auth_logrotate(FILESYSTEM)
        result['etc/nginx/conf.d/md-next-security.conf']=security.SECURITY_CONFIG.encode()
        result['etc/nginx/md-next-panel-bans.map']=files['etc/nginx/md-next-panel-bans.map']
    config='var/lib/md-next-adguard/AdGuardHome.yaml'
    if config in result:
        spec=importlib.util.spec_from_file_location('portable_components',Path(__file__).with_name('migration-components.py'))
        components=importlib.util.module_from_spec(spec);spec.loader.exec_module(components)
        try:result[config]=components.adguard_config(result[config])
        except Exception as error:raise events.MigrationError('MIG_ADGUARD_CONFIG') from error
    result[name]=target.encode()
    return result


def upgrade_database(path,env_blob=b'',*,migrate=True):
    import io
    from dotenv import dotenv_values
    values=dotenv_values(stream=io.StringIO(env_blob.decode('utf-8')),interpolate=False)
    environment=dict(os.environ)
    for key in ('JWT_SECRET_KEY','XRAY_PRIVATE_KEY','XRAY_PUBLIC_KEY','SERVER_HOST','XRAY_SERVER_NAME','PANEL_PUBLIC_URL'):
        if values.get(key) is not None:environment[key]=values[key]
    if migrate:
        result=subprocess.run([str(ROOT/'backend/venv/bin/alembic'),'-x','url=sqlite+aiosqlite:///'+str(path),'upgrade','head'],
                              cwd=ROOT/'backend',env=environment,capture_output=True,text=True,timeout=300)
        if result.returncode:raise events.MigrationError('MIG_SCHEMA')
    code = """import sqlite3,sys
from app.services.crypto import decrypt_node_secret,encrypt_node_secret
with sqlite3.connect(sys.argv[1]) as db:
    rows=db.execute("SELECT id,secret FROM nodes").fetchall()
    for identifier,secret in rows:
        db.execute("UPDATE nodes SET secret=? WHERE id=?",(encrypt_node_secret(decrypt_node_secret(secret)),identifier))
"""
    result = subprocess.run([str(ROOT/'backend/venv/bin/python'), '-c', code, str(path)],
                            cwd=ROOT/'backend', env=environment, capture_output=True, text=True, timeout=120)
    if result.returncode:raise events.MigrationError('MIG_SCHEMA')


def restore(blob, password, *, rollback=False, target_address=None):
    spec = importlib.util.spec_from_file_location("migration_postflight", Path(__file__).with_name("migration-postflight.py"))
    postflight = importlib.util.module_from_spec(spec); spec.loader.exec_module(postflight)
    bundle = library()
    bundle.guard_memory(len(blob)*7+96*1024*1024,FILESYSTEM)
    try:manifest, database, files = bundle.decode_bundle(blob, password)
    except ValueError as error:raise events.MigrationError('MIG_BUNDLE') from error
    portable=manifest.get('restore_mode')=='clean-install' and not rollback
    try:
        certificate_links = postflight.certbot_link_plan(files)
        certificate_executables = postflight.certbot_executable_plan(files, manifest)
    except (ValueError, KeyError) as error: raise events.MigrationError('MIG_CERTBOT_LAYOUT') from error
    if portable:
        def version(value):
            if not re.fullmatch(r'\d+\.\d+\.\d+',value):raise events.MigrationError('MIG_SCHEMA')
            return tuple(map(int,value.split('.')))
        if version(manifest['version'])>version((ROOT/'backend/VERSION').read_text().strip()):raise events.MigrationError('MIG_SCHEMA')
    if not portable and manifest['version'] != (ROOT/'backend/VERSION').read_text().strip():
        raise RuntimeError('Установите на новом VPS ту же версию панели')
    if not portable and manifest['commit'] != (ROOT/'DEPLOYED_COMMIT').read_text().strip():
        raise RuntimeError('Сборки серверов различаются')
    with tempfile.TemporaryDirectory(prefix='mdnext-restore-') as temporary:
        db_file = Path(temporary)/'db'; db_file.write_bytes(database)
        inventory = bundle.inventory_database(db_file)
        if inventory != {key:manifest[key] for key in ('revision', 'tables')}:
            raise RuntimeError('Манифест базы не совпадает со снимком')
        if not rollback:
            try: postflight.rebind_dns(db_file, target_address)
            except ValueError as error: raise events.MigrationError('MIG_TARGET_ADDRESS') from error
        if portable:
            upgrade_database(db_file,files['opt/md-next/backend/.env'])
            database=db_file.read_bytes()
        elif inventory['revision'] in {'0021_bind_node_secrets','0022_persist_host_identity'}:
            # Восстановление той же сборки и откат также получают переносимые
            # секреты. Схему при откате не повышаем, только привязываем ключи.
            upgrade_database(db_file,files['opt/md-next/backend/.env'],migrate=False)
            database=db_file.read_bytes()
    current = ROOT/'backend/md_next.db'
    target_db = current.resolve()
    if current.is_symlink() and target_db != (FILESYSTEM/'var/lib/md-next/data/md_next.db'):
        raise RuntimeError('Неизвестное расположение базы')
    previous = json.loads((STATE/'restored.json').read_text()) if (STATE/'restored.json').is_file() else None
    pending = json.loads((STATE/'importing.json').read_text()) if (STATE/'importing.json').is_file() else {}
    if not rollback:
        if not (ROOT/'backend/.env').is_file(): raise RuntimeError('Сначала подготовьте новую установку')
        if 'MDNEXT_MIGRATION_STAGING=1' not in (ROOT/'backend/.env').read_text().splitlines():
            raise RuntimeError('Восстановление разрешено только в подготовленную, изолированную установку')
        old = bundle.inventory_database(target_db)
        same_staging = previous and previous.get('installation') == manifest.get('installation') and not (STATE/'activated').exists()
        if any(old['tables'].get(t,0) for t in ('clients','client_profiles','nodes')) and not same_staging:
            raise RuntimeError('На целевом сервере уже есть клиенты или ноды')
        if not (STATE/'before-restore.mdmove').exists() or not previous and not pending:
            backup, _ = bundle.capture(FILESYSTEM, password)
            write(STATE/'before-restore.mdmove', backup)
        else:
            # A previous cancelled run may have used a different password. Fail
            # before overwriting anything if this baseline cannot be recovered.
            bundle.decode_bundle((STATE/'before-restore.mdmove').read_bytes(), password)
    if portable:files=clean_install_files(files)
    files=component_capabilities(files)
    try: files=postflight.renewal_files(files)
    except ValueError as error: raise events.MigrationError('MIG_CERTBOT_LAYOUT') from error
    # Validate every destination before replacing the first file.
    for name in files:
        path = FILESYSTEM/name
        if any(p.is_symlink() for p in [path, *path.parents]):
            # Certbot live paths are intentionally materialised to avoid links in bundles.
            if name.startswith('etc/letsencrypt/') and path.is_symlink() and path.resolve().is_relative_to(FILESYSTEM/'etc/letsencrypt'):
                continue
            raise RuntimeError('Небезопасный целевой путь')
    component_files={'opt/md-next-adguard/AdGuardHome','etc/systemd/system/md-next-adguard.service'} if portable and manifest.get('components',{}).get('adguard') else set()
    if component_files and not (STATE/'adguard-owned').exists():
        if any((FILESYSTEM/name).exists() or (FILESYSTEM/name).is_symlink() for name in component_files):
            raise events.MigrationError('MIG_TARGET_OCCUPIED')
    try:
        (STATE/'verified.json').unlink(missing_ok=True)
        timer_state=STATE/'certbot-timer.json'
        if not timer_state.exists():
            write(timer_state,json.dumps({'active':command('systemctl','is-active','certbot.timer',check=False).returncode==0,'enabled':command('systemctl','is-enabled','certbot.timer',check=False).returncode==0}).encode())
        timer_stop=command('systemctl','disable','--now','certbot.timer',check=False)
        if timer_stop.returncode:raise events.MigrationError('MIG_CERTBOT_LAYOUT',exit_code=timer_stop.returncode)
        if command('systemctl','is-active','certbot.service',check=False).returncode==0:
            raise events.MigrationError('MIG_CERTBOT_BUSY')
        for service in SERVICES:command('systemctl','stop',service,check=False)
        for service in SERVICES:
            if command('systemctl','is-active',service,check=False).returncode==0:
                raise RuntimeError('Не удалось остановить целевые службы')
        if not rollback:
            write(STATE/'importing.json',json.dumps({'files':sorted(set(files)|component_files|set(pending.get('files',[])))}).encode())
        if rollback:
            if 'etc/systemd/system/md-next-adguard.service' in set((previous or {}).get('imported_files',[]))|set(pending.get('files',[])):
                command('systemctl','stop','md-next-adguard',check=False)
            # Remove only paths introduced by this import, absent from its baseline.
            # Do not leave source client keys/configs behind after cancelling.
            for name in set((previous or {}).get('imported_files',[])) | set(pending.get('files',[])):
                if name not in files:
                    if not bundle.allowed_path(name):raise RuntimeError('Недопустимый путь отката')
                    path=FILESYSTEM/name
                    if any(p.is_symlink() for p in path.parents):raise RuntimeError('Небезопасный путь отката')
                    if path.is_symlink() and not (name.startswith('etc/letsencrypt/live/') and path.resolve().is_relative_to(FILESYSTEM/'etc/letsencrypt/archive')):
                        raise RuntimeError('Небезопасная ссылка отката')
                    path.unlink(missing_ok=True)
        for name, content in files.items():
            path = FILESYSTEM/name
            if path.is_symlink(): path.unlink()
            if name == 'opt/md-next/backend/.env': content = staging_env(content, True)
            write(path, content, 0o755 if name in certificate_executables else 0o644 if name.endswith('fullchain.pem') else 0o600)
        postflight.restore_certbot_links(FILESYSTEM, certificate_links)
        write(target_db, database)
        for suffix in ('-wal','-shm'):
            extra = Path(str(target_db)+suffix)
            if extra.exists(): extra.unlink()
        command('systemctl','daemon-reload')
        for name in ('xray.json','awg0.conf'):
            old_stage=(FILESYSTEM/'var/lib/md-next/app-config')/name
            if old_stage.is_symlink(): raise RuntimeError('Ссылка в staging-конфигурации')
            old_stage.unlink(missing_ok=True)
        command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
        import pwd
        xray_user=command('systemctl','show','xray','-p','User','--value').stdout.strip() or 'root'
        group=pwd.getpwnam(xray_user).pw_gid
        for path in [(FILESYSTEM/'usr/local/etc/xray/config.json'), *(FILESYSTEM/'usr/local/etc/xray/tls').glob('*.pem')]:
            if path.is_file():os.chown(path,0,group);path.chmod(0o640)
        if component_files:
            spec=importlib.util.spec_from_file_location('migration_components',Path(__file__).with_name('migration-components.py'))
            components=importlib.util.module_from_spec(spec);spec.loader.exec_module(components)
            if not (STATE/'adguard-owned').exists():write(STATE/'adguard-owned',b'1')
            try:components.prepare_adguard(manifest,FILESYSTEM,write,command)
            except Exception as error:raise events.MigrationError('MIG_ADGUARD') from error
        validate_components()
        if not rollback:
            write(STATE/'restored.json',json.dumps({**manifest,'imported_files':sorted(set(files)|component_files)}).encode())
        else:
            (STATE/'adguard-owned').unlink(missing_ok=True)
            (STATE/'restored.json').unlink(missing_ok=True)
        (STATE/'verified.json').unlink(missing_ok=True)
        command('systemctl','restart','nginx')
        command('systemctl','start','md-next-backend')
        (STATE/'importing.json').unlink(missing_ok=True)
        if rollback:
            timer_flags=json.loads(timer_state.read_text())
            if timer_flags.get('enabled'):command('systemctl','enable','certbot.timer')
            if timer_flags.get('active'):
                command('systemctl','start','certbot.timer')
            timer_state.unlink(missing_ok=True)
    except Exception as exc:
        # Keep services fenced. Never start a half-restored VPN or a second bot.
        for service in SERVICES: command('systemctl','stop',service,check=False)
        if getattr(exc,'code',None):raise
        raise RuntimeError('Восстановление не прошло проверку. Службы остановлены; доступен rollback с паролем комплекта.') from exc
    return manifest


def main():
    parser=argparse.ArgumentParser(description='Переезд MD-Next; DNS переключается вручную')
    parser.add_argument('action',choices=['export','inspect','restore','verify','activate','rollback','resume-source','api-export'])
    parser.add_argument('--file',type=Path)
    parser.add_argument('--target-address',help='IP целевого VPS для DNS подписок')
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--confirm-cutover',action='store_true')
    parser.add_argument('--confirm-protocol-tests',action='store_true')
    parser.add_argument('--password-stdin',action='store_true')
    args=parser.parse_args()
    if fcntl is None or not hasattr(os,'geteuid') or os.geteuid()!=0: raise RuntimeError('Требуется root на Linux')
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE/'lock').open('w') as lock, (STATE.parent/'maintenance.lock').open('w') as maintenance:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        fcntl.flock(maintenance,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action=='verify':
            if (STATE/'importing.json').exists():raise events.MigrationError('MIG_RESTORE')
            if not (STATE/'restored.json').is_file():raise RuntimeError('Сначала восстановите комплект')
            if 'MDNEXT_MIGRATION_STAGING=1' not in (ROOT/'backend/.env').read_text().splitlines():raise RuntimeError('Проверка разрешена только в изолированном режиме')
            # Explicitly synchronise only VPN; do not enter FastAPI lifespan or start the bot.
            code="""import os,asyncio
from dotenv import load_dotenv
load_dotenv('/opt/md-next/backend/.env',override=True)
import sqlite3,importlib.util,time,json
from contextlib import closing
def fail(code):
 print('MDNEXT_EVENT '+json.dumps({'code':code}),flush=True)
 raise SystemExit(1)
with closing(sqlite3.connect('file:/opt/md-next/backend/md_next.db?mode=ro',uri=True)) as source:
 values=dict(source.execute('SELECT key,value FROM settings'))
if values.get('warp.uplink.enabled')=='true':
 spec=importlib.util.spec_from_file_location('trusted_uplink','/usr/local/lib/md-next/warp-uplink-runtime.py')
 uplink=importlib.util.module_from_spec(spec);spec.loader.exec_module(uplink)
 uplink.start(int(values['active_node_id']),int(values.get('warp.uplink.port','40000')))
 deadline=time.monotonic()+900
 while time.monotonic()<deadline:
  status=uplink.status()
  if status.get('phase')=='ready':break
  if status.get('phase')=='failed':fail('MIG_WARP')
  time.sleep(3)
 else:fail('MIG_WARP')
if values.get('adguard.enabled')=='true':
 import urllib.request
 try:
  with urllib.request.urlopen('http://127.0.0.1:3001/',timeout=5) as response:
   if response.status!=200:fail('MIG_ADGUARD')
 except Exception:fail('MIG_ADGUARD')
spec=importlib.util.spec_from_file_location('trusted_migration','/usr/local/lib/md-next/migrate-panel.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
runtime.drop_to_panel_user()
from app.db.database import AsyncSessionLocal
from app.services.client_service import ClientService
from app.services.awg import AWGService
from app.services.nginx import apply_xhttp_tls_path
async def main():
 if values.get('profiles.enabled.vless_xhttp_tls','false').lower() in {'true','1','yes'}:
  try:await apply_xhttp_tls_path(values['profiles.path.vless_xhttp_tls'])
  except Exception:fail('MIG_NGINX_CONFIG')
 async with AsyncSessionLocal() as db:
  for sync,code in ((ClientService.sync_xray_clients,'MIG_XRAY_APPLY'),(AWGService.sync_server_config,'MIG_AWG_CONFIG')):
   ok,reason=await sync(db)
   if not ok:fail(code)
asyncio.run(main())
"""
            result=subprocess.run([str(ROOT/'backend/venv/bin/python'),'-c',code],cwd=ROOT/'backend',capture_output=True,text=True,timeout=1100)
            if result.returncode:
                items=[events.parse_event(line) for line in result.stdout.splitlines()]
                detail=next((item for item in reversed(items) if item and item['code'].startswith('MIG_')),None)
                raise events.MigrationError(detail['code'] if detail else 'MIG_VERIFY')
            command('nginx','-t')
            env={}
            for line in (ROOT/'backend/.env').read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value=line.split('=',1);env[key]=value.strip().strip('"').strip("'")
            from urllib.parse import urlsplit
            domain=urlsplit(env.get('PANEL_PUBLIC_URL','')).hostname
            if not domain:raise RuntimeError('Не задан домен панели')
            command('curl','--fail','--silent','--show-error','--noproxy','*','--max-time','15','--resolve',domain+':443:127.0.0.1','https://'+domain+'/')
            prepare_fail2ban()
            database=(ROOT/'backend/md_next.db').resolve()
            write(STATE/'verified.json',json.dumps({'at':time.time(),'database':database_digest(),'configuration':configuration_digest()}).encode())
            print('Проверены применение VPN и HTTPS панели. До активации проверьте каждый протокол с внешнего устройства, направив его на новый IP.');return
        if args.action=='activate':
            if (STATE/'importing.json').exists():raise events.MigrationError('MIG_RESTORE')
            if not args.confirm_cutover or not (STATE/'restored.json').is_file():
                raise RuntimeError('Сначала восстановите комплект и подтвердите финальное переключение')
            manifest=json.loads((STATE/'restored.json').read_text())
            if not manifest.get('frozen'): raise RuntimeError('Пробный комплект нельзя активировать: нужен финальный снимок после остановки исходной панели')
            verified=json.loads((STATE/'verified.json').read_text())
            if not args.confirm_protocol_tests or not 0<=time.time()-verified['at']<=3600 or verified['database']!=database_digest() or verified.get('configuration')!=configuration_digest():
                raise RuntimeError('Повторите verify и подтвердите внешнюю проверку протоколов')
            command('nginx','-t')
            command('/usr/local/bin/xray','run','-test','-config','/usr/local/etc/xray/config.json')
            if has_panel_security():
                result = command('/usr/bin/python3', str(ROOT/'scripts/install-security.py'), '--fail2ban-only', check=False)
                if result.returncode:
                    raise events.MigrationError('MIG_FAIL2BAN', exit_code=result.returncode)
            if manifest.get('components',{}).get('adguard'):
                command('systemctl','enable','md-next-adguard')
            write(STATE/'activated',b'Activation started; automatic rollback forbidden.\n')
            path=ROOT/'backend/.env';write(path,staging_env(path.read_bytes(),False),0o640)
            command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'))
            start_services()
            command('systemctl','enable','--now','certbot.timer')
            write(STATE/'activated',b'Activated; rollback requires manual database reconciliation.\n')
            print('Службы активированы. Проверьте профили из внешней сети и переключите DNS вручную.');return
        if args.action=='resume-source':
            if not args.confirm_cutover or not (STATE/'source-fenced.json').exists() or (STATE/'source-activated.json').exists():
                raise RuntimeError('Возобновление требует остановленного целевого сервера и исходной изолированной панели до активации')
            path=ROOT/'backend/.env';write(path,staging_env(path.read_bytes(),False),0o640)
            command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
            start_services()
            (STATE/'source-fenced.json').unlink()
            print('Исходные службы запущены. Проверьте их состояние.');return
        password=json.loads(sys.stdin.read())['password'] if args.action=='api-export' or args.password_stdin else getpass.getpass('Пароль комплекта: ')
        bundle=library()
        if args.action in ('export','api-export'):
            active=[]
            original_env=(ROOT/'backend/.env').read_bytes() if args.freeze else None
            if args.freeze:
                if not args.confirm_cutover:raise RuntimeError('Финальная остановка требует --confirm-cutover')
                for service in SERVICES:
                    if command('systemctl','is-active',service,check=False).returncode==0:active.append(service)
                write(STATE/'source-fenced.json',b'{"manual":true}')
                for service in active:command('systemctl','stop',service)
            try:
                blob,manifest=bundle.capture(FILESYSTEM,password,frozen=args.freeze)
                if args.action=='api-export':
                    import uuid,pwd
                    target=ROOT/'backend/migrations'/ (uuid.uuid4().hex+'.mdmove');write(target,blob)
                    user=pwd.getpwnam('md-next');os.chown(target.parent,user.pw_uid,user.pw_gid);os.chown(target,user.pw_uid,user.pw_gid)
                    print(json.dumps({'name':target.name,'created_at':manifest['created_at'],'size':len(blob),'final':False}))
                else:
                    if not args.file:raise RuntimeError('Укажите --file')
                    if args.file.exists():raise RuntimeError('Файл уже существует')
                    write(args.file.absolute(),blob);print('Зашифрованный комплект сохранён.')
                if args.freeze:
                    env_path=ROOT/'backend/.env';write(env_path,staging_env(env_path.read_bytes(),True),0o640)
                    command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                    command('systemctl','start','md-next-backend')
            except Exception:
                if original_env is not None:
                    write(ROOT/'backend/.env',original_env,0o640)
                    command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                for service in active:command('systemctl','start',service,check=False)
                if args.freeze:(STATE/'source-fenced.json').unlink(missing_ok=True)
                raise
        elif args.action=='inspect':
            if not args.file:raise RuntimeError('Укажите --file')
            manifest,_,_=bundle.decode_bundle(args.file.read_bytes(),password);print(json.dumps(manifest,ensure_ascii=False,indent=2))
        elif args.action=='restore':
            if not args.file:raise RuntimeError('Укажите --file')
            restore(args.file.read_bytes(),password,target_address=args.target_address);print('Комплект восстановлен в изолированном режиме. Бот и VPN не запущены.')
        elif args.action=='rollback':
            if (STATE/'activated').exists():raise RuntimeError('После активации требуется ручное согласование баз: автоматический откат заблокирован')
            restore((STATE/'before-restore.mdmove').read_bytes(),password,rollback=True)
            print('Целевая установка возвращена в исходный изолированный режим.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Command output, paths and exceptions can contain secrets. Keep them off stdout/logs.
        action=sys.argv[1] if len(sys.argv)>1 else ''
        fallback={'export':'MIG_SOURCE_CONFIG','api-export':'MIG_SOURCE_CONFIG','inspect':'MIG_BUNDLE',
                  'restore':'MIG_RESTORE','rollback':'MIG_RESTORE','verify':'MIG_VERIFY','activate':'MIG_VERIFY'}.get(action,'MIG_INTERNAL')
        if isinstance(error,ValueError) and 'памяти' in str(error):fallback='MIG_MEMORY'
        print(events.event(events.public_error(error,fallback)['code']),flush=True)
        raise SystemExit(1)
