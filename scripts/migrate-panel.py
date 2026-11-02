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
            with os.fdopen(fd,'wb') as stream:stream.write(blob)
            os.replace(temporary,path.name,src_dir_fd=directory,dst_dir_fd=directory)
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
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def staging_env(content, staging):
    lines = [line for line in content.decode().splitlines() if not line.startswith('MDNEXT_MIGRATION_STAGING=')]
    if staging: lines.append('MDNEXT_MIGRATION_STAGING=1')
    return ('\n'.join(lines)+'\n').encode()


def restore(blob, password, *, rollback=False):
    bundle = library(); manifest, database, files = bundle.decode_bundle(blob, password)
    if manifest['version'] != (ROOT/'backend/VERSION').read_text().strip():
        raise RuntimeError('Установите на новом VPS ту же версию панели')
    if manifest['commit'] != (ROOT/'DEPLOYED_COMMIT').read_text().strip():
        raise RuntimeError('Сборки серверов различаются')
    with tempfile.TemporaryDirectory(prefix='mdnext-restore-') as temporary:
        db_file = Path(temporary)/'db'; db_file.write_bytes(database)
        inventory = bundle.inventory_database(db_file)
        if inventory != {key:manifest[key] for key in ('revision', 'tables')}:
            raise RuntimeError('Манифест базы не совпадает со снимком')
    current = ROOT/'backend/md_next.db'
    target_db = current.resolve()
    if current.is_symlink() and target_db != (FILESYSTEM/'var/lib/md-next/data/md_next.db'):
        raise RuntimeError('Неизвестное расположение базы')
    if not rollback:
        if not (ROOT/'backend/.env').is_file(): raise RuntimeError('Сначала подготовьте новую установку')
        if 'MDNEXT_MIGRATION_STAGING=1' not in (ROOT/'backend/.env').read_text():
            raise RuntimeError('Восстановление разрешено только в подготовленную, изолированную установку')
        old = bundle.inventory_database(target_db)
        previous = json.loads((STATE/'restored.json').read_text()) if (STATE/'restored.json').is_file() else None
        same_staging = previous and previous.get('installation') == manifest.get('installation') and not (STATE/'activated').exists()
        if any(old['tables'].get(t,0) for t in ('clients','client_profiles','nodes')) and not same_staging:
            raise RuntimeError('На целевом сервере уже есть клиенты или ноды')
        if not (STATE/'before-restore.mdmove').exists():
            backup, _ = bundle.capture(FILESYSTEM, password)
            write(STATE/'before-restore.mdmove', backup)
    for service in SERVICES: command('systemctl','stop',service,check=False)
    # Validate every destination before replacing the first file.
    for name in files:
        path = FILESYSTEM/name
        if any(p.is_symlink() for p in [path, *path.parents]):
            # Certbot live paths are intentionally materialised to avoid links in bundles.
            if name.startswith('etc/letsencrypt/') and path.is_symlink() and path.resolve().is_relative_to('/etc/letsencrypt'):
                continue
            raise RuntimeError('Небезопасный целевой путь')
    try:
        for name, content in files.items():
            path = FILESYSTEM/name
            if path.is_symlink(): path.unlink()
            if name == 'opt/md-next/backend/.env': content = staging_env(content, True)
            write(path, content, 0o644 if name.endswith('fullchain.pem') else 0o600)
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
        command('nginx','-t')
        command('/usr/local/bin/xray','run','-test','-config','/usr/local/etc/xray/config.json')
        if not rollback:
            write(STATE/'restored.json',json.dumps(manifest).encode())
        else:
            (STATE/'restored.json').unlink(missing_ok=True)
        (STATE/'verified.json').unlink(missing_ok=True)
        command('systemctl','restart','nginx')
        command('systemctl','start','md-next-backend')
    except Exception as exc:
        # Keep services fenced. Never start a half-restored VPN or a second bot.
        for service in SERVICES: command('systemctl','stop',service,check=False)
        raise RuntimeError('Восстановление не прошло проверку. Службы остановлены; доступен rollback с паролем комплекта.') from exc
    return manifest


def main():
    parser=argparse.ArgumentParser(description='Переезд MD-Next; DNS переключается вручную')
    parser.add_argument('action',choices=['export','inspect','restore','verify','activate','rollback','resume-source','api-export'])
    parser.add_argument('--file',type=Path)
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
            if not (STATE/'restored.json').is_file():raise RuntimeError('Сначала восстановите комплект')
            if 'MDNEXT_MIGRATION_STAGING=1' not in (ROOT/'backend/.env').read_text():raise RuntimeError('Проверка разрешена только в изолированном режиме')
            # Explicitly synchronise only VPN; do not enter FastAPI lifespan or start the bot.
            code="""import os,asyncio
from dotenv import load_dotenv
load_dotenv('/opt/md-next/backend/.env',override=True)
import sqlite3,importlib.util,time
from contextlib import closing
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
  if status.get('phase')=='failed':raise RuntimeError('WARP preparation failed')
  time.sleep(3)
 else:raise RuntimeError('WARP preparation timed out')
if values.get('adguard.enabled')=='true':
 import urllib.request
 with urllib.request.urlopen('http://127.0.0.1:3001/',timeout=5) as response:
  if response.status!=200:raise RuntimeError('AdGuard requires separate preparation')
from app.db.database import AsyncSessionLocal
from app.services.client_service import ClientService
from app.services.awg import AWGService
async def main():
 async with AsyncSessionLocal() as db:
  for sync in (ClientService.sync_xray_clients,AWGService.sync_server_config):
   ok,reason=await sync(db)
   if not ok:raise RuntimeError('VPN verification failed')
asyncio.run(main())
"""
            subprocess.run([str(ROOT/'backend/venv/bin/python'),'-c',code],cwd=ROOT/'backend',check=True,capture_output=True,timeout=1100)
            command('nginx','-t')
            env={}
            for line in (ROOT/'backend/.env').read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value=line.split('=',1);env[key]=value.strip().strip('"').strip("'")
            from urllib.parse import urlsplit
            domain=urlsplit(env.get('PANEL_PUBLIC_URL','')).hostname
            if not domain:raise RuntimeError('Не задан домен панели')
            command('curl','--fail','--silent','--show-error','--noproxy','*','--max-time','15','--resolve',domain+':443:127.0.0.1','https://'+domain+'/')
            database=(ROOT/'backend/md_next.db').resolve()
            write(STATE/'verified.json',json.dumps({'at':time.time(),'database':hashlib.sha256(database.read_bytes()).hexdigest()}).encode())
            print('Проверены применение VPN и HTTPS панели. До активации проверьте каждый протокол с внешнего устройства, направив его на новый IP.');return
        if args.action=='activate':
            if not args.confirm_cutover or not (STATE/'restored.json').is_file():
                raise RuntimeError('Сначала восстановите комплект и подтвердите финальное переключение')
            manifest=json.loads((STATE/'restored.json').read_text())
            if not manifest.get('frozen'): raise RuntimeError('Пробный комплект нельзя активировать: нужен финальный снимок после остановки исходной панели')
            verified=json.loads((STATE/'verified.json').read_text())
            if not args.confirm_protocol_tests or time.time()-verified['at']>3600 or verified['database']!=hashlib.sha256((ROOT/'backend/md_next.db').resolve().read_bytes()).hexdigest():
                raise RuntimeError('Повторите verify и подтвердите внешнюю проверку протоколов')
            command('nginx','-t')
            command('/usr/local/bin/xray','run','-test','-config','/usr/local/etc/xray/config.json')
            write(STATE/'activated',b'Activation started; automatic rollback forbidden.\n')
            path=ROOT/'backend/.env';write(path,staging_env(path.read_bytes(),False),0o640)
            command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'))
            for service in reversed(SERVICES): command('systemctl','start',service,check=False)
            command('systemctl','restart','md-next-backend')
            write(STATE/'activated',b'Activated; rollback requires manual database reconciliation.\n')
            print('Службы активированы. Проверьте профили из внешней сети и переключите DNS вручную.');return
        if args.action=='resume-source':
            if not args.confirm_cutover or not (STATE/'source-fenced.json').exists() or (STATE/'source-activated.json').exists():
                raise RuntimeError('Возобновление требует остановленного целевого сервера и исходной изолированной панели до активации')
            path=ROOT/'backend/.env';write(path,staging_env(path.read_bytes(),False),0o640)
            command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
            for service in reversed(SERVICES):command('systemctl','start',service,check=False)
            command('systemctl','restart','md-next-backend')
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
            restore(args.file.read_bytes(),password);print('Комплект восстановлен в изолированном режиме. Бот и VPN не запущены.')
        elif args.action=='rollback':
            if (STATE/'activated').exists():raise RuntimeError('После активации требуется ручное согласование баз: автоматический откат заблокирован')
            restore((STATE/'before-restore.mdmove').read_bytes(),password,rollback=True)
            print('Целевая установка возвращена в исходный изолированный режим.')


if __name__=='__main__':
    try:main()
    except Exception:
        # Command output, paths and exceptions can contain secrets. Keep them off stdout/logs.
        print('Операция переезда не завершена. Проверьте этап, пароль и состояние служб; DNS не изменялся.',file=sys.stderr)
        raise SystemExit(1)
