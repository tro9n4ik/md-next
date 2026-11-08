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
import importlib.util
import urllib.request
import threading
import uuid

spec=importlib.util.spec_from_file_location('migration_events',Path(__file__).with_name('migration-events.py'))
events=importlib.util.module_from_spec(spec);spec.loader.exec_module(events)

STATE=Path('/var/lib/md-next/migration')
LIB=Path('/usr/local/lib/md-next')
ROOT=Path('/opt/md-next')
ACTIVE_GUARD=None


class NetworkGuard:
    """Fresh pinned SSH renews a target lease; the target enforces a hard cap."""
    def __init__(self,request,token,machine):
        self.request,self.token,self.machine=request,token,machine
        self.owner=threading.get_ident();self.error=None;self.done=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True)

    def start(self):
        global ACTIVE_GUARD
        ACTIVE_GUARD=self;self.thread.start()

    def check(self):
        if self.error:raise events.MigrationError('MIG_NETWORK_GUARD') from None

    def renew(self):
        sock,transport=connect_target(self.request,timeout=5)
        try:
            if execute(transport,'cat /etc/machine-id',timeout=5).strip()!=self.machine:
                raise events.MigrationError('MIG_TARGET_BINDING')
            if not self.done.is_set():
                execute(transport,'/usr/bin/python3 /var/lib/md-next/network-latch/'+self.token+'/restore.py renew '+self.token,timeout=10)
        finally:transport.close();sock.close()

    def loop(self):
        while not self.done.wait(40):
            try:self.renew()
            except Exception as error:
                self.error=error;return

    def stop(self):
        global ACTIVE_GUARD
        self.done.set()
        if self.thread.ident is not None:self.thread.join(timeout=40)
        if ACTIVE_GUARD is self:ACTIVE_GUARD=None


def fingerprint(key):
    return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def public_domain(value):
    try:
        host=value.strip().rstrip('.').encode('idna').decode('ascii').lower()
    except (UnicodeError,AttributeError):return False
    try:ipaddress.ip_address(host)
    except ValueError:
        label=r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
        return len(host)<=253 and bool(re.fullmatch(r'(?:'+label+r'\.)+'+label,host)) and not host.endswith('.localhost')
    return False


def validate(request):
    if request.get('action') not in {'prepare','finalize','activate','commit','cancel','reconcile'}:raise ValueError('Invalid action')
    ip=ipaddress.ip_address(request['host'])
    if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip,'scope_id',None):raise ValueError('Нужен публичный IP нового VPS')
    if type(request.get('port')) is not int or not 1<=request['port']<=65535:raise ValueError('Invalid SSH port')
    if not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}',request.get('fingerprint','')):raise ValueError('Invalid fingerprint')
    if not isinstance(request.get('ssh_password'),str) or not 1<=len(request['ssh_password'])<=512:raise ValueError('Invalid SSH password')
    if not isinstance(request.get('password'),str) or not 12<=len(request['password'])<=256:raise ValueError('Invalid package password')
    if request.get('confirm') is not True:raise ValueError('Confirmation required')
    if request['action'] in {'activate','commit'} and request.get('confirm_protocol_tests') is not True:raise ValueError('External protocol test required')
    return str(ip)


def write_json(path,value):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.migration-',dir=path.parent)
    with os.fdopen(fd,'w') as output:
        json.dump(value,output,ensure_ascii=False);output.flush();os.fsync(output.fileno())
    os.chmod(name,0o600);os.replace(name,path)
    if os.name=='posix':
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)


def status(phase,message,request,**details):
    previous={}
    try:previous=json.loads((STATE/'job.json').read_text())
    except (OSError,ValueError):pass
    operation=request.get('operation_id')
    same=previous.get('operation_id')==operation if operation else previous.get('action')==request['action']
    history=previous.get('history',[]) if same else []
    stage=details.pop('stage',phase)
    entry={'stage':stage,'message':message,'at':time.time()}
    if not history or (history[-1].get('stage'),history[-1].get('message'))!=(stage,message):history=(history+[entry])[-80:]
    write_json(STATE/'job.json',{'phase':phase,'stage':stage,'message':message,'history':history,
        'host':request['host'],'port':request['port'],'action':request['action'],'operation_id':operation,
        'actor_id':request.get('actor_id'),'updated_at':time.time(),**details})


def fail(error,request):
    previous={}
    try:previous=json.loads((STATE/'job.json').read_text())
    except (OSError,ValueError):pass
    stage=previous.get('stage','source_preflight')
    defaults={'source_preflight':'MIG_SOURCE_CONFIG','connecting':'MIG_SSH_CONNECT',
              'restoring':'MIG_RESTORE','verifying':'MIG_VERIFY','activating':'MIG_ACTIVATION_PENDING',
              'acknowledging':'MIG_ACTIVATION_PENDING','reconciling':'MIG_ACTIVATION_PENDING','cancelling':'MIG_RESTORE'}
    details=events.public_error(error,defaults.get(stage,'MIG_INTERNAL'))
    code=details.pop('code');message=details.pop('message')
    status('reboot_required' if code=='MIG_REBOOT_REQUIRED' else 'error',message,request,
           stage=stage,error_code=code,**details)


def load_runtime():
    import importlib.util
    spec=importlib.util.spec_from_file_location('migration_runtime',LIB/'migrate-panel.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def execute(ssh,command,stdin='',timeout=240,progress=None):
    channel=ssh.open_session(timeout=15);channel.settimeout(15);channel.exec_command(command)
    if stdin:channel.sendall(stdin.encode())
    channel.shutdown_write();deadline=time.monotonic()+timeout;output=bytearray();pending=b'';remote_error=None
    try:
        while True:
            if ACTIVE_GUARD is not None and ACTIVE_GUARD.owner==threading.get_ident():ACTIVE_GUARD.check()
            if time.monotonic()>deadline:raise events.MigrationError('MIG_TIMEOUT')
            if channel.recv_ready():
                chunk=channel.recv(65536)
                if len(output)<65536:output.extend(chunk[:65536-len(output)])
                pending+=chunk
                while b'\n' in pending:
                    line,pending=pending.split(b'\n',1)
                    item=events.parse_event(line.decode('utf-8','replace'))
                    if item:
                        if item['code'].startswith('MIG_'):remote_error=item['code']
                        elif progress:progress(item)
                if len(pending)>1024:pending=b''
            if channel.recv_stderr_ready():channel.recv_stderr(65536) # Never persist remote command logs/secrets.
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():break
            time.sleep(.05)
        exit_code=channel.recv_exit_status()
        if exit_code!=0:raise events.MigrationError(remote_error or 'MIG_REMOTE_EXIT',exit_code=exit_code)
        return output.decode(errors='replace')
    finally:channel.close()


def remote_cli(transport,action,password,extra='',context=None):
    cmd='/opt/md-next/backend/venv/bin/python /usr/local/lib/md-next/migrate-panel.py '+action+' --password-stdin '+extra
    timeout={'verify':1200,'activation-status':30,'prepare-session':60}.get(action,300)
    return execute(transport,cmd,json.dumps({'password':password,**({'context':context} if context else {})}),timeout=timeout)


def connect_target(request,timeout=15):
    import paramiko
    sock=socket.create_connection((request['host'],request['port']),timeout=timeout)
    transport=paramiko.Transport(sock);transport.banner_timeout=timeout;transport.auth_timeout=20
    try:
        transport.start_client(timeout=timeout)
        if fingerprint(transport.get_remote_server_key())!=request['fingerprint']:raise events.MigrationError('MIG_SSH_KEY')
        transport.auth_password('root',request['ssh_password'])
        if not transport.is_authenticated():raise events.MigrationError('MIG_SSH_AUTH')
        transport.set_keepalive(20)
        return sock,transport
    except Exception:
        transport.close();sock.close();raise


def cleanup_transfer(request, transport, directory, target_id):
    command = 'rm -rf -- ' + shlex.quote(directory)
    try:
        execute(transport, command, timeout=15)
        return
    except Exception:
        # The original transport can be unusable after a failed upload.
        pass
    try:
        sock, recovered = connect_target(request, timeout=5)
        try:
            actual = execute(recovered, 'cat /etc/machine-id', timeout=10).strip()
            if not target_id or actual != target_id:
                raise events.MigrationError('MIG_TARGET_BINDING')
            execute(recovered, command, timeout=15)
        finally:
            recovered.close()
            sock.close()
    except Exception:
        print('Не удалось удалить временный каталог переноса на целевом VPS.', file=sys.stderr)


def arm_network(transport, sftp, directory):
    sftp.put(str(LIB / "controller-probe.py"), directory + "/controller-probe.py")
    sftp.chmod(directory + "/controller-probe.py",0o600)
    target = directory + "/network-latch.py"
    sftp.put(str(LIB / "network-latch.py"), target)
    sftp.chmod(target, 0o600)
    result = json.loads(execute(transport, "/usr/bin/python3 " + shlex.quote(target) + " arm"))
    token = result.get("token", "")
    if not re.fullmatch("[a-f0-9]{32}", token):
        raise RuntimeError("Не удалось включить страховку сети")
    execute(transport,"cp -- " + shlex.quote(directory+"/controller-probe.py") + " /var/lib/md-next/network-latch/" + token + "/controller-probe.py",timeout=5)
    return token


def confirm_network(request, token):
    # Новая SSH-сессия проверяет доступ извне, а не только живое старое соединение.
    sock, transport = connect_target(request, timeout=5)
    try:
        env = (ROOT / "backend/.env").read_text()
        match = re.search(r"(?m)^PANEL_PUBLIC_URL=(.+)$", env)
        url = match.group(1).strip().strip('"').strip("'").rstrip("/") if match else ""
        from urllib.parse import urlsplit
        if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            raise RuntimeError("Нет HTTPS-адреса контроллера")
        # Resolve on the controller in a killable process; target never performs DNS.
        code = "import socket,sys; a=socket.getaddrinfo(sys.argv[1],None,type=socket.SOCK_STREAM); print((next((e[4][0] for e in a if e[0]==socket.AF_INET),a[0][4][0])))"
        if request.get('controller_address'):
            address=str(ipaddress.ip_address(request['controller_address']))
        else:
            result = subprocess.run([sys.executable,"-c",code,urlsplit(url).hostname],capture_output=True,text=True,timeout=5.0,check=True)
            address = str(ipaddress.ip_address(result.stdout.strip()))
        # The independently copied probe enforces its own 2-second wall-clock budget.
        # Kept inside the independent latch directory, beyond worker cleanup.
        probe = "/var/lib/md-next/network-latch/" + token + "/controller-probe.py"
        execute(transport,"/usr/bin/python3 " + shlex.quote(probe) + " --url " + shlex.quote(url) + " --ip " + shlex.quote(address),timeout=5)
        restore = "/var/lib/md-next/network-latch/" + token + "/restore.py"
        execute(transport, "/usr/bin/python3 " + shlex.quote(restore) + " confirm " + token, timeout=25)
    finally:
        transport.close()
        sock.close()


def reboot_target(request,sock,transport):
    before=execute(transport,'cat /proc/sys/kernel/random/boot_id').strip()
    machine=execute(transport,'cat /etc/machine-id').strip()
    status('rebooting',events.MESSAGES['TARGET_REBOOT_START'],request,stage='TARGET_REBOOT_START')
    execute(transport,'systemd-run --quiet --unit=md-next-migration-reboot --on-active=3 /usr/bin/systemctl reboot',timeout=15)
    transport.close();sock.close()
    status('rebooting',events.MESSAGES['TARGET_REBOOT_WAIT'],request,stage='TARGET_REBOOT_WAIT')
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        candidate_sock=candidate=None;keep=False
        try:
            candidate_sock,candidate=connect_target(request,timeout=5)
            after=execute(candidate,'cat /proc/sys/kernel/random/boot_id',timeout=10).strip()
            if after and after!=before:
                if execute(candidate,'cat /etc/machine-id',timeout=10).strip()!=machine:raise events.MigrationError('MIG_TARGET_BINDING')
                status('preparing',events.MESSAGES['TARGET_RECONNECTED'],request,stage='TARGET_RECONNECTED')
                keep=True
                return candidate_sock,candidate
        except Exception as error:
            if getattr(error,'code',None) in {'MIG_SSH_KEY','MIG_SSH_AUTH','MIG_TARGET_BINDING'} or type(error).__name__=='AuthenticationException':raise
        finally:
            if candidate is not None and not keep:
                candidate.close();candidate_sock.close()
        time.sleep(3)
    raise events.MigrationError('MIG_REBOOT_TIMEOUT')


def finish_session():
    spec=importlib.util.spec_from_file_location('migration_session',LIB/'migration-session.py')
    session=importlib.util.module_from_spec(spec);spec.loader.exec_module(session)
    session.finish(STATE,write_json)


def receipt_context(saved,*,final=True):
    names={'session':'session_id','installation':'installation','commit':'target_commit',
           'machine':'target_machine'}
    if final:names['snapshot']='final_snapshot'
    if any(not saved.get(key) for key in names.values()):raise events.MigrationError('MIG_TARGET_VERSION')
    return {key:saved[value] for key,value in names.items()}


def acknowledge_activation(request,saved,*,retry=False):
    """Reconcile a receipt; never invoke activate or un-fence the source."""
    context=receipt_context(saved)
    pending=json.loads((STATE/'source-activated.json').read_text())
    if any(pending.get(key)!=saved.get(key) for key in ('session_id','installation','target_commit','target_machine','final_snapshot')):
        raise events.MigrationError('MIG_TARGET_BINDING')
    token=pending.get('guard')
    if not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):raise events.MigrationError('MIG_TARGET_VERSION')
    context={**context,'guard':token}
    deadline=time.monotonic()+(90 if retry else 0)
    while True:
        try:
            sock,transport=connect_target(request,timeout=5)
            try:
                if execute(transport,'cat /etc/machine-id',timeout=5).strip()!=context['machine']:
                    raise events.MigrationError('MIG_TARGET_BINDING')
                result=json.loads(remote_cli(transport,'activation-status',request['password'],context=context))
                if any(result.get(k)!=v for k,v in context.items()):raise events.MigrationError('MIG_TARGET_BINDING')
                if result.get('phase')!='completed':raise events.MigrationError('MIG_ACTIVATION_PENDING')
                guard=json.loads(execute(transport,'/usr/bin/python3 /var/lib/md-next/network-latch/'+token+'/restore.py status '+token,timeout=10))
            finally:transport.close();sock.close()
            if guard.get('status')=='armed' and guard.get('live') is True:
                confirm_network(request,token)
            elif guard.get('status')!='confirmed':raise events.MigrationError('MIG_NETWORK_GUARD')
            write_json(STATE/'source-activated.json',{'state':'completed',**saved})
            finish_session()
            status('activated','Новый VPS активирован. Замените IP в DNS-записях ваших доменов и проверьте подключения.',request,stage='completed')
            return
        except Exception as error:
            if getattr(error,'code',None) in {'MIG_TARGET_BINDING','MIG_SSH_KEY','MIG_SSH_AUTH','MIG_NETWORK_GUARD'} or time.monotonic()>=deadline:raise
            time.sleep(3)


def latest_build():
    base='https://api.github.com/repos/tro9n4ik/md-next/'
    def api(path):
        request=urllib.request.Request(base+path,headers={'User-Agent':'MD-Next-migration'})
        with urllib.request.urlopen(request,timeout=25) as response:return json.load(response)
    try:
        runs=api('actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=10')['workflow_runs']
        for run in runs:
            sha=run['head_sha']
            if not re.fullmatch('[0-9a-f]{40}',sha):continue
            commit=api('commits/'+sha)
            if not commit['commit']['verification']['verified']:continue
            checks=api('actions/runs?head_sha='+sha+'&per_page=100')['workflow_runs']
            successful=True
            for path in ('.github/workflows/ci.yml','.github/workflows/secrets.yml'):
                matching=[r for r in checks if r['path']==path and r['event']=='push']
                if not matching or max(matching,key=lambda r:r.get('id',0))['conclusion']!='success':successful=False
            if successful:return sha
        raise ValueError()
    except Exception as error:raise events.MigrationError('MIG_BUILD') from error


def run(request):
    import paramiko
    validate(request)
    if not request.get('operation_id'):request={**request,'operation_id':uuid.uuid4().hex}
    if request['action']=='commit':
        # Подтверждение охватывает свежую финальную синхронизацию и активацию.
        run({**request,'action':'finalize'})
        run({**request,'action':'activate'})
        return
    if request['action']=='prepare':(STATE/'target-ready.json').unlink(missing_ok=True)
    status('exporting','Проверяем исходную установку.',request,stage='source_preflight')
    runtime=load_runtime();STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    binding_path=STATE/'target.json'
    binding={key:request[key] for key in ('host','port','fingerprint')}
    if request['action']!='prepare':
        saved=json.loads(binding_path.read_text()) if binding_path.is_file() else {}
        if any(saved.get(k)!=v for k,v in binding.items()):
            raise events.MigrationError('MIG_TARGET_BINDING')
        check=hashlib.scrypt(request['password'].encode(),salt=bytes.fromhex(saved['salt']),n=2**14,r=8,p=1).hex()
        if not hmac.compare_digest(check,saved['password_check']):raise events.MigrationError('MIG_TARGET_BINDING')
    status('connecting','Проверяем ключ SSH нового VPS.',request)
    sock,transport=connect_target(request)
    remote_dir=None
    latch_token=None
    guard=None
    try:
        # Never prepare this same machine even when addressed through a second public IP.
        local_id=Path('/etc/machine-id').read_text().strip()
        target_id=execute(transport,'cat /etc/machine-id').strip()
        if target_id==local_id:raise events.MigrationError('MIG_SAME_SERVER')
        try:commit=(ROOT/'DEPLOYED_COMMIT').read_text().strip()
        except OSError as error:raise events.MigrationError('MIG_BUILD') from error
        if not re.fullmatch('[0-9a-f]{40}',commit):raise events.MigrationError('MIG_BUILD')
        sftp=paramiko.SFTPClient.from_transport(transport)
        if hasattr(sftp,'get_channel'):sftp.get_channel().settimeout(60)
        if request['action']!='prepare' and saved.get('target_machine') and saved['target_machine']!=target_id:
            raise events.MigrationError('MIG_TARGET_BINDING')
        if request['action'] in {'finalize','activate'} and execute(transport,'cat /opt/md-next/DEPLOYED_COMMIT').strip()!=saved.get('target_commit'):
            raise events.MigrationError('MIG_BUILD')
        if request['action'] in {'finalize','activate'}:receipt_context(saved,final=False)
        if request['action']=='prepare':
            # Проверяем идентичность источника без создания снимка до установки.
            env_text=(ROOT/'backend/.env').read_text()
            identity=re.search(r'(?m)^JWT_SECRET_KEY=(.+)$',env_text)
            if not identity:raise events.MigrationError('MIG_SOURCE_CONFIG')
            installation=hashlib.sha256(identity.group(1).encode()).hexdigest()
            preparation=STATE/'preparation.json'
            # Новый явный запуск подготовки может включать исправленный билд.
            # Внутри одного запуска и после перезагрузки выбранный SHA неизменен.
            commit=latest_build()
            write_json(preparation,{**binding,'installation':installation,'commit':commit})
            status('preparing','Устанавливаем чистый проверенный билд GitHub. DNS не меняется.',request)
            env={}
            for line in (ROOT/'backend/.env').read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key,value=line.split('=',1);env[key]=value.strip().strip('"').strip("'")
            from urllib.parse import urlsplit
            main=env.get('SERVER_HOST','');panel=urlsplit(env.get('PANEL_PUBLIC_URL','')).hostname or ''
            email=request.get('email','')
            if email and not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}',email):raise events.MigrationError('MIG_INPUT')
            for domain in (main,panel):
                if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}',domain):raise events.MigrationError('MIG_SOURCE_ADDRESS')
            for attempt in range(3):
                # /root survives reboot, but upload anew after each reconnect.
                remote_dir=execute(transport,'umask 077; mktemp -d /root/mdnext-transfer-XXXXXXXX').strip()
                if not re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):raise RuntimeError('Invalid remote directory')
                for name in ('prepare-migration-target.sh','migration-target.py','migration-events.py','migration-dns.py'):
                    sftp.put(str(LIB/name),remote_dir+'/'+name)
                command='bash '+shlex.quote(remote_dir+'/prepare-migration-target.sh')+' '+' '.join(shlex.quote(s) for s in (commit,main,panel,email,installation))
                try:
                    execute(transport,command,timeout=6000,progress=lambda item:status('preparing',item['message'],request,stage=item['code']))
                    break
                except events.MigrationError as error:
                    if error.code!='MIG_REBOOT_REQUIRED' or attempt==2:raise
                    execute(transport,'rm -rf -- '+shlex.quote(remote_dir),timeout=15);remote_dir=None
                    sftp.close()
                    sock,transport=reboot_target(request,sock,transport)
                    sftp=paramiko.SFTPClient.from_transport(transport)
            salt=os.urandom(16)
            # SSH_CLIENT pins the original controller address before DNS cutover.
            client=execute(transport,"printf '%s' \"$SSH_CLIENT\"",timeout=5).split()
            controller=str(ipaddress.ip_address(client[0])) if client else None
            if controller and not ipaddress.ip_address(controller).is_global:controller=None
            saved={**binding,'target_commit':commit,'installation':installation,'target_machine':target_id,
                   'session_id':hmac.new(request['password'].encode(),(json.dumps(binding,sort_keys=True)+installation+commit).encode(),hashlib.sha256).hexdigest()[:32],'controller_address':controller,
                   'salt':salt.hex(),'password_check':hashlib.scrypt(request['password'].encode(),salt=salt,n=2**14,r=8,p=1).hex()}
            try:remote_cli(transport,'prepare-session',request['password'],context=receipt_context(saved,final=False))
            except Exception as error:raise events.MigrationError('MIG_TARGET_VERSION') from error
            write_json(binding_path,saved)
            preparation.unlink(missing_ok=True)
        if request['action'] in {'prepare','finalize','activate'}:
            try: execute(transport,'test -f /usr/local/lib/md-next/migration-postflight.py && test -f /usr/local/lib/md-next/migration-dns.py',timeout=5)
            except Exception as error: raise events.MigrationError('MIG_TARGET_VERSION') from error
        request={**request,'controller_address':saved.get('controller_address')}
        if request['action'] in {'prepare','finalize'}:
            status('exporting','Создаём полный зашифрованный комплект.',request)
            frozen=request['action']=='finalize'
            if frozen:
                import sqlite3
                from contextlib import closing
                with closing(sqlite3.connect('file:/opt/md-next/backend/md_next.db?mode=ro',uri=True)) as db:
                    row=db.execute("SELECT value FROM settings WHERE key='protocol.reality.server_address'").fetchone()
                if not row or not public_domain(row[0].strip()):raise events.MigrationError('MIG_SOURCE_ADDRESS')
            if frozen:
                if (STATE/'source-activated.json').exists():raise events.MigrationError('MIG_STATE')
                # Record intent before stopping the first service so interrupted jobs are recoverable.
                write_json(STATE/'source-fenced.json',{'host':request['host']})
                for service in runtime.SERVICES:runtime.command('systemctl','stop',service,check=False)
                for service in runtime.SERVICES:
                    if runtime.command('systemctl','is-active',service,check=False).returncode==0:raise RuntimeError('Не удалось остановить исходные службы')
            if frozen:
                try:blob,manifest=runtime.library().capture(Path('/'),request['password'],frozen=True,portable=True)
                except (ValueError,MemoryError) as error:
                    raise events.MigrationError('MIG_MEMORY' if isinstance(error,MemoryError) or 'памяти' in str(error) else 'MIG_SOURCE_CONFIG') from error
            else:
                try:blob,manifest=runtime.library().capture(Path('/'),request['password'],portable=True)
                except (ValueError,MemoryError) as error:
                    raise events.MigrationError('MIG_MEMORY' if isinstance(error,MemoryError) or 'памяти' in str(error) else 'MIG_SOURCE_CONFIG') from error
            expected_installation=installation if not frozen else saved.get('installation')
            if manifest.get('installation')!=expected_installation:raise events.MigrationError('MIG_SOURCE_CONFIG')
            if not frozen:runtime.write(STATE/'trial.mdmove',blob)
            if frozen:
                env_path=ROOT/'backend/.env'
                runtime.write(env_path,runtime.staging_env(env_path.read_bytes(),True),0o640)
                runtime.command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                runtime.command('systemctl','start','md-next-backend')
                saved={**saved,'final_snapshot':hashlib.sha256(blob).hexdigest()}
                write_json(binding_path,saved)
            if remote_dir is None:
                remote_dir=execute(transport,'umask 077; mktemp -d /root/mdnext-transfer-XXXXXXXX').strip()
                if not re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):raise RuntimeError('Invalid remote directory')
            with tempfile.TemporaryDirectory(prefix='mdnext-transfer-',dir=STATE) as folder:
                latch_token=arm_network(transport,sftp,remote_dir)
                guard=NetworkGuard(request,latch_token,target_id);guard.start()
                bundle=Path(folder)/'transfer.mdmove';runtime.write(bundle,blob)
                # Keep the final encrypted snapshot for disaster recovery.
                if frozen:runtime.write(STATE/'final.mdmove',blob)
                remote=remote_dir+'/transfer.mdmove';sftp.put(str(bundle),remote);sftp.chmod(remote,0o600)
                status('restoring','Восстанавливаем комплект в изолированную целевую установку.',request)
                remote_cli(transport,'restore',request['password'],'--file '+shlex.quote(remote)+' --target-address '+shlex.quote(request['host']))
            status('verifying','Проверяем конфигурации VPN и HTTPS на новом VPS.',request)
            try: execute(transport,'/usr/bin/python3 /usr/local/lib/md-next/migration-dns.py --check',timeout=120)
            except Exception as error: raise events.MigrationError('MIG_TARGET_DNS') from error
            remote_cli(transport,'verify',request['password'],context=receipt_context(saved) if frozen else None)
            guard.check();guard.stop();guard=None
            confirm_network(request,latch_token)
            latch_token=None
            if not frozen:write_json(STATE/'target-ready.json',binding)
            status('awaiting_activation' if frozen else 'prepared',
                   'Финальный снимок перенесён. Исходная панель изолирована. Проверьте протоколы на новом IP и подтвердите активацию.' if frozen else
                   'Пробный перенос выполнен. Новый VPS изолирован; исходная панель работает. Проверьте протоколы, затем выполните финальную синхронизацию.',request)
        elif request['action']=='activate':
            if not (STATE/'source-fenced.json').exists():raise RuntimeError('Исходная панель не остановлена')
            context=receipt_context(saved)
            remote_dir=execute(transport,'umask 077; mktemp -d /root/mdnext-transfer-XXXXXXXX').strip()
            if not re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):raise RuntimeError('Invalid remote directory')
            latch_token=arm_network(transport,sftp,remote_dir)
            guard=NetworkGuard(request,latch_token,target_id);guard.start()
            context={**context,'guard':latch_token}
            status('verifying','Повторно проверяем финальный снимок и готовность нового VPS.',request,stage='verifying')
            try: execute(transport,'/usr/bin/python3 /usr/local/lib/md-next/migration-dns.py --check',timeout=120)
            except Exception as error: raise events.MigrationError('MIG_TARGET_DNS') from error
            remote_cli(transport,'verify',request['password'],context=context)
            guard.check()
            try:guard.renew()
            except Exception as error:raise events.MigrationError('MIG_NETWORK_GUARD') from error
            status('activating','Активируем проверенный финальный снимок на новом VPS.',request,stage='activating')
            write_json(STATE/'source-activated.json',{'state':'activation_started',**saved,'guard':latch_token})
            try:remote_cli(transport,'activate',request['password'],'--confirm-cutover --confirm-protocol-tests',context=context)
            except Exception:
                # A lost SSH reply does not prove that activation failed.
                pass
            guard.check();guard.stop();guard=None
            status('acknowledging','Проверяем подтверждение активации и новое SSH-соединение.',request,stage='acknowledging')
            acknowledge_activation(request,saved,retry=True)
            latch_token=None
        elif request['action']=='reconcile':
            if not (STATE/'source-fenced.json').exists() or not (STATE/'source-activated.json').exists():raise events.MigrationError('MIG_STATE')
            status('reconciling','Проверяем результат активации без повторного запуска служб.',request,stage='reconciling')
            acknowledge_activation(request,saved,retry=True)
        else:
            if (STATE/'source-activated.json').exists():raise RuntimeError('После активации требуется согласование данных')
            status('cancelling','Отменяем подготовку и проверяем исходное состояние нового VPS.',request,stage='cancelling')
            if saved.get('session_id'):
                remote_cli(transport,'cancel-target',request['password'],context=receipt_context(saved,final=False))
            else:remote_cli(transport,'rollback',request['password'])
            if (STATE/'source-fenced.json').exists():
                path=ROOT/'backend/.env';runtime.write(path,runtime.staging_env(path.read_bytes(),False),0o640)
                runtime.command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                runtime.start_services()
                (STATE/'source-fenced.json').unlink()
            binding_path.unlink(missing_ok=True)
            (STATE/'target-ready.json').unlink(missing_ok=True)
            finish_session()
            status('cancelled','Целевой сервер возвращён в изолированное исходное состояние; исходные службы возобновлены. Проверьте DNS и подключения.',request)
    finally:
        if guard is not None:guard.stop()
        if remote_dir and re.fullmatch('/root/mdnext-transfer-[A-Za-z0-9]+',remote_dir):
            cleanup_transfer(request, transport, remote_dir, target_id)
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
    except Exception as error:
        if (STATE/'source-fenced.json').exists() and not (STATE/'source-activated.json').exists():
            try:
                runtime=load_runtime();path=ROOT/'backend/.env'
                runtime.write(path,runtime.staging_env(path.read_bytes(),True),0o640)
                runtime.command('/usr/bin/python3',str(ROOT/'scripts/install-privilege-separation.py'),'--configure-only')
                runtime.command('systemctl','start','md-next-backend')
            except Exception:pass
        fail(error,request)
        raise SystemExit(1)
