#!/usr/bin/env python3
"""Root-owned SSH migration worker. Credentials are consumed once, never logged."""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time

STATE=Path('/var/lib/md-next/migration')
LIB=Path('/usr/local/lib/md-next')
ROOT=Path('/opt/md-next')


def fingerprint(key):
    return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def validate(request):
    if request.get('action') not in {'prepare','finalize','activate','cancel'}:raise ValueError('Invalid action')
    ip=ipaddress.ip_address(request['host'])
    if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip,'scope_id',None):raise ValueError('Нужен публичный IP нового VPS')
    if type(request.get('port')) is not int or not 1<=request['port']<=65535:raise ValueError('Invalid SSH port')
    if not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}',request.get('fingerprint','')):raise ValueError('Invalid fingerprint')
    if not isinstance(request.get('ssh_password'),str) or not 1<=len(request['ssh_password'])<=512:raise ValueError('Invalid SSH password')
    if not isinstance(request.get('password'),str) or not 12<=len(request['password'])<=256:raise ValueError('Invalid package password')
    if request.get('confirm') is not True:raise ValueError('Confirmation required')
    if request['action']=='activate' and request.get('confirm_protocol_tests') is not True:raise ValueError('External protocol test required')
    return str(ip)


def write_json(path,value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.migration-',dir=path.parent)
    with os.fdopen(fd,'w') as output:json.dump(value,output,ensure_ascii=False)
    os.chmod(name,0o600);os.replace(name,path)


def status(phase,message,request):
    write_json(STATE/'job.json',{'phase':phase,'message':message,'host':request['host'],'action':request['action'],'actor_id':request.get('actor_id'),'updated_at':time.time()})


def load_runtime():
    import importlib.util
    spec=importlib.util.spec_from_file_location('migration_runtime',LIB/'migrate-panel.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def execute(ssh,command,stdin='',timeout=240):
    channel=ssh.open_session(timeout=15);channel.settimeout(15);channel.exec_command(command)
    if stdin:channel.sendall(stdin.encode())
    channel.shutdown_write();deadline=time.monotonic()+timeout;output=bytearray()
    try:
        while True:
            if time.monotonic()>deadline:raise RuntimeError('Истекло время удалённой операции')
            if channel.recv_ready():
                chunk=channel.recv(65536)
                if len(output)<65536:output.extend(chunk[:65536-len(output)])
            if channel.recv_stderr_ready():channel.recv_stderr(65536) # Never persist remote command logs/secrets.
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
            time.sleep(.05)
        if channel.recv_exit_status()!=0:raise RuntimeError('Удалённая операция не прошла проверку')
        return output.decode(errors='replace')
    finally:channel.close()


def remote_cli(transport,action,password,extra=''):
    cmd='/opt/md-next/backend/venv/bin/python /usr/local/lib/md-next/migrate-panel.py '+action+' --password-stdin '+extra
    return execute(transport,cmd,json.dumps({'password':password}),timeout=1200 if action=='verify' else 300)


def run(request):
    import paramiko
    validate(request)
    runtime=load_runtime();STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    trial_blob=trial_manifest=None
    if request['action'] in {'prepare','finalize'}:
        # Fail on unsupported/missing source files before installing or stopping anything.
        trial_blob,trial_manifest=runtime.library().capture(Path('/'),request['password'])
        if request['action']=='finalize':
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect('file:/opt/md-next/backend/md_next.db?mode=ro',uri=True)) as db:
                row=db.execute("SELECT value FROM settings WHERE key='protocol.reality.server_address'").fetchone()
            if row:
                value=row[0].strip()
                try:ipaddress.ip_address(value)
                except ValueError:
                    if '.' not in value or value=='localhost':raise RuntimeError('Не задан публичный домен профилей')
                else:raise RuntimeError('Автоматическое переключение профилей с IP не поддерживается. Сначала подготовьте перенос адреса или обновление профилей.')
    binding_path=STATE/'target.json'
    binding={key:request[key] for key in ('host','port','fingerprint')}
    if request['action']!='prepare':
        saved=json.loads(binding_path.read_text()) if binding_path.is_file() else {}
        if any(saved.get(k)!=v for k,v in binding.items()):
            raise RuntimeError('Целевой VPS не совпадает с подготовленным')
        check=hashlib.scrypt(request['password'].encode(),salt=bytes.fromhex(saved['salt']),n=2**14,r=8,p=1).hex()
        if not hmac.compare_digest(check,saved['password_check']):raise RuntimeError('Пароль комплекта изменён')
    status('connecting','Проверяем ключ SSH нового VPS.',request)
    sock=socket.create_connection((request['host'],request['port']),timeout=15)
    transport=paramiko.Transport(sock);transport.banner_timeout=15;transport.auth_timeout=20
    remote_dir=None
    try:
        transport.start_client(timeout=15)
        if fingerprint(transport.get_remote_server_key())!=request['fingerprint']:
            raise RuntimeError('Ключ SSH изменился. Пароль серверу не отправлен.')
        transport.auth_password('root',request['ssh_password'])
        if not transport.is_authenticated():raise RuntimeError('SSH authentication failed')
        transport.set_keepalive(20)
        # Never prepare this same machine even when addressed through a second public IP.
        local_id=Path('/etc/machine-id').read_text().strip()
        if execute(transport,'cat /etc/machine-id').strip()==local_id:raise RuntimeError('Выбран исходный сервер')
        commit=(ROOT/'DEPLOYED_COMMIT').read_text().strip()
        if not re.fullmatch('[0-9a-f]{40}',commit):raise RuntimeError('Сборка исходного сервера не определена')
        sftp=paramiko.SFTPClient.from_transport(transport)
        if request['action']=='prepare':
            status('preparing','Подготавливаем чистый VPS той же сборки. DNS не меняется.',request)
            env={}
            for line in (ROOT/'backend/.env').read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value=line.split('=',1);env[key]=value.strip().strip('"').strip("'")
            from urllib.parse import urlsplit
            main=env.get('SERVER_HOST','');panel=urlsplit(env.get('PANEL_PUBLIC_URL','')).hostname or ''
            email=request.get('email','')
            if not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}',email):raise RuntimeError('Укажите email администратора')
            for domain in (main,panel):
                if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}',domain):raise RuntimeError('Для подготовки нужны домены панели и входа')
            # A private temporary upload directory is created by a fixed remote command.
            remote_dir=execute(transport,'umask 077; mktemp -d /root/mdnext-transfer-XXXXXXXX').strip()
            if not re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):raise RuntimeError('Invalid remote directory')
            sftp.put(str(LIB/'prepare-migration-target.sh'),remote_dir+'/prepare.sh')
            command='bash '+shlex.quote(remote_dir+'/prepare.sh')+' '+' '.join(shlex.quote(s) for s in (commit,main,panel,email))
            execute(transport,command,timeout=2400)
            salt=os.urandom(16)
            write_json(binding_path,{**binding,'salt':salt.hex(),'password_check':hashlib.scrypt(request['password'].encode(),salt=salt,n=2**14,r=8,p=1).hex()})
        if request['action'] in {'prepare','finalize'}:
            status('exporting','Создаём полный зашифрованный комплект.',request)
            frozen=request['action']=='finalize'
            if frozen:
                if (STATE/'source-activated.json').exists():raise RuntimeError('Переезд уже активирован')
                # Record intent before stopping the first service so interrupted jobs are recoverable.
                write_json(STATE/'source-fenced.json',{'host':request['host']})
                for service in runtime.SERVICES:runtime.command('systemctl','stop',service,check=False)
                for service in runtime.SERVICES:
                    if runtime.command('systemctl','is-active',service,check=False).returncode==0:raise RuntimeError('Не удалось остановить исходные службы')
            if frozen:blob,manifest=runtime.library().capture(Path('/'),request['password'],frozen=True)
            else:blob,manifest=trial_blob,trial_manifest
            if frozen:
                env_path=ROOT/'backend/.env'
                runtime.write(env_path,runtime.staging_env(env_path.read_bytes(),True),0o640)
                runtime.command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                runtime.command('systemctl','start','md-next-backend')
            if remote_dir is None:
                remote_dir=execute(transport,'umask 077; mktemp -d /root/mdnext-transfer-XXXXXXXX').strip()
                if not re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):raise RuntimeError('Invalid remote directory')
            with tempfile.TemporaryDirectory(prefix='mdnext-transfer-',dir=STATE) as folder:
                bundle=Path(folder)/'transfer.mdmove';runtime.write(bundle,blob)
                # Keep the final encrypted snapshot for disaster recovery.
                if frozen:runtime.write(STATE/'final.mdmove',blob)
                remote=remote_dir+'/transfer.mdmove';sftp.put(str(bundle),remote);sftp.chmod(remote,0o600)
                status('restoring','Восстанавливаем комплект в изолированную целевую установку.',request)
                remote_cli(transport,'restore',request['password'],'--file '+shlex.quote(remote))
            status('verifying','Проверяем конфигурации VPN и HTTPS на новом VPS.',request)
            remote_cli(transport,'verify',request['password'])
            status('awaiting_activation' if frozen else 'prepared',
                   'Финальный снимок перенесён. Исходная панель изолирована. Проверьте протоколы на новом IP и подтвердите активацию.' if frozen else
                   'Пробный перенос выполнен. Новый VPS изолирован; исходная панель работает. Проверьте протоколы, затем выполните финальную синхронизацию.',request)
        elif request['action']=='activate':
            if not (STATE/'source-fenced.json').exists():raise RuntimeError('Исходная панель не остановлена')
            remote_cli(transport,'verify',request['password'])
            write_json(STATE/'source-activated.json',{'state':'activation_started',**binding})
            remote_cli(transport,'activate',request['password'],'--confirm-cutover --confirm-protocol-tests')
            write_json(STATE/'source-activated.json',binding)
            status('activated','Новый VPS активирован. Переключите DNS вручную и проверьте подключения. Автоматический обратный перенос после активации запрещён.',request)
        else:
            if (STATE/'source-activated.json').exists():raise RuntimeError('После активации требуется согласование данных')
            remote_cli(transport,'rollback',request['password'])
            if (STATE/'source-fenced.json').exists():
                path=ROOT/'backend/.env';runtime.write(path,runtime.staging_env(path.read_bytes(),False),0o640)
                runtime.command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                for service in reversed(runtime.SERVICES):runtime.command('systemctl','start',service,check=False)
                runtime.command('systemctl','restart','md-next-backend')
                (STATE/'source-fenced.json').unlink()
            binding_path.unlink(missing_ok=True)
            status('cancelled','Целевой сервер возвращён в изолированное исходное состояние; исходные службы возобновлены. Проверьте DNS и подключения.',request)
    finally:
        if remote_dir and re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):
            try:execute(transport,'rm -rf -- '+shlex.quote(remote_dir),timeout=15)
            except Exception:pass
        transport.close();sock.close()


if __name__=='__main__':
    request_path=Path(sys.argv[1])
    if request_path.parent!=STATE or not re.fullmatch(r'request-[0-9a-f]{32}\.json',request_path.name):raise SystemExit(1)
    request=json.loads(request_path.read_text());request_path.unlink()
    try:
        import fcntl
        with (STATE.parent/'maintenance.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            run(request)
    except Exception:
        if (STATE/'source-fenced.json').exists() and not (STATE/'source-activated.json').exists():
            try:
                runtime=load_runtime();path=ROOT/'backend/.env'
                runtime.write(path,runtime.staging_env(path.read_bytes(),True),0o640)
                runtime.command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                runtime.command('systemctl','start','md-next-backend')
            except Exception:pass
        status('error','Этап не завершён. DNS не менялся. Исходный VPS мог остаться изолирован после финальной синхронизации; не активируйте оба сервера. Проверьте этап и выполните отмену до активации.',request)
        raise SystemExit(1)
