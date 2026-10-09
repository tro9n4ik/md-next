#!/usr/bin/python3
"""Root-owned, finite privileged operations. No shell or caller-selected executable."""
import asyncio
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import ssl

LIB = Path('/usr/local/lib/md-next')
STAGE = Path('/var/lib/md-next/app-config')
XRAY = Path('/usr/local/etc/xray/config.json')
AWG = Path('/etc/amnezia/amneziawg/awg0.conf')
SERVICES = {'xray', 'nginx', 'md-next-backend', 'md-next-awg', 'warp-svc', 'md-next-panel-update.service',
            'md-next-adguard', 'fail2ban', 'certbot', 'awg-quick@awg0'}


def read_stage(value, prefix):
    path = Path(value)
    if path.parent != STAGE or not path.name.startswith(prefix):
        raise ValueError('Invalid staging path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != pwd.getpwnam('md-next').pw_uid or info.st_size > 4 * 1024 * 1024:
            raise ValueError('Invalid staging file')
        return os.read(fd, 4 * 1024 * 1024 + 1)
    finally:
        os.close(fd)


def validate_xray(data):
    config = json.loads(data)
    if config.get('log', {}) != {'loglevel': 'warning'}:
        raise ValueError('File logging is forbidden')
    baseline = json.loads(XRAY.read_text())
    allowed_files = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'certificateFile', 'keyFile'}: allowed_files.add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value: collect(item)
    collect(baseline)
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'certificateFile', 'keyFile'} and item not in allowed_files:
                    raise ValueError('Certificate path is not owned by this installation')
                if key.lower().endswith(('file', 'path')) and key not in {'certificateFile', 'keyFile', 'path'}:
                    raise ValueError('Unexpected file setting')
                inspect(item)
        elif isinstance(value, list):
            for item in value: inspect(item)
        elif isinstance(value, str) and value.startswith('ext:'):
            if not re.fullmatch(r'ext:(?:geoip|geosite)\.dat:[A-Za-z0-9_-]+', value):
                raise ValueError('External asset path forbidden')
    inspect(config)
    for inbound in config.get('inbounds', []):
        if inbound.get('protocol') == 'tun':
            name = inbound.get('settings', {}).get('name', '')
            if name != 'mdawg' and not re.fullmatch(r'mdat[0-9a-f]{1,11}', name):
                raise ValueError('Unexpected TUN interface')
    return config


def validate_awg(data):
    text = data.decode('utf8')
    interface = {'PrivateKey', 'Address', 'ListenPort', 'Jc', 'Jmin', 'Jmax', 'S1', 'S2', 'S3', 'S4',
                 'H1', 'H2', 'H3', 'H4', 'HeaderProtectionKey', 'ContentPaddingAddition', 'RandomTrailers', 'DisableCookies'}
    section, seen = None, set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        if line in {'[Interface]', '[Peer]'}:
            section, seen = line, set(); continue
        if '=' not in line: raise ValueError('Invalid AWG line')
        key, value = (part.strip() for part in line.split('=', 1))
        allowed = interface if section == '[Interface]' else {'PublicKey', 'AllowedIPs'} if section == '[Peer]' else set()
        if key not in allowed or key in seen: raise ValueError('AWG hook or duplicate setting forbidden')
        seen.add(key)
        if key in {'Address', 'AllowedIPs'}: ipaddress.ip_interface(value)
        elif key in {'PrivateKey', 'PublicKey', 'HeaderProtectionKey'}:
            if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', value): raise ValueError('Invalid key')
        elif key in {'RandomTrailers', 'DisableCookies'}:
            if value not in {'on', 'off'}: raise ValueError('Invalid switch')
        elif not value.isdecimal(): raise ValueError('Invalid number')
    return text


def write_root(path, data):
    metadata = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(prefix='.md-next-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(data)
        os.chmod(name, stat.S_IMODE(metadata.st_mode) if metadata else 0o600)
        if metadata: os.chown(name, metadata.st_uid, metadata.st_gid)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def execute(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=100, env={
        'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})


def dispatch(request):
    operation = request.get('operation')
    if operation == 'certificate-days':
        env = {}
        for line in Path('/opt/md-next/backend/.env').read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1); env[key] = value.strip().strip('"').strip("'")
        path = env.get('TLS_CERT_PATH') or '/etc/letsencrypt/live/'+env['SERVER_HOST']+'/fullchain.pem'
        expires = ssl._ssl._test_decode_cert(path)['notAfter']
        return [0, str(int((ssl.cert_time_to_seconds(expires)-time.time())//86400)), '']
    if operation == 'update-status':
        path = Path('/var/lib/md-next/updates/status.json')
        state = json.loads(path.read_text()) if path.exists() else {'phase': 'idle', 'message': 'Обновление ещё не запускалось.'}
        fields = {'phase', 'message', 'commit', 'backup', 'started_at', 'finished_at'}
        return [0, json.dumps({k: v for k, v in state.items() if k in fields}), '']
    if operation == 'update-start':
        commit, token = request.get('commit'), request.get('token', '')
        if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit) or not isinstance(token, str) or len(token) > 1024:
            raise ValueError('Invalid update request')
        spec = importlib.util.spec_from_file_location('trusted_updater', LIB/'update-panel.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        state = Path('/var/lib/md-next/updates');state.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {'commit': commit, 'token': token, 'python': '/usr/bin/python3'}
        module.Updater(Path('/opt/md-next'), state, payload).verify_revision(token)
        active = execute(['/usr/bin/systemctl', 'is-active', 'md-next-panel-update.service'])
        if active.stdout.strip() in {'active', 'activating'}: raise ValueError('Update is running')
        path = state/('request-'+uuid.uuid4().hex+'.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream: json.dump(payload, stream)
        result = execute(['/usr/bin/systemd-run', '--unit=md-next-panel-update', '--collect', '--property=RuntimeMaxSec=1800', '--',
                          '/usr/bin/python3', str(LIB/'update-panel.py'), '--request', str(path)])
        if result.returncode: path.unlink(missing_ok=True); raise RuntimeError('Update dispatch failed')
        queued = {'phase': 'queued', 'commit': commit, 'started_at': time.time(), 'message': 'Подготовка обновления.'}
        return [0, json.dumps(queued), '']
    if operation == 'persist-xray':
        data = read_stage(str(STAGE/'xray.json'), 'xray.json'); validate_xray(data); write_root(XRAY, data)
        return [0, '', '']
    if operation == 'nginx':
        kind, value = request.get('kind'), request.get('value')
        if kind == 'sni':
            if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', value): raise ValueError('Invalid SNI')
        elif kind == 'path':
            if not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9/_-]{0,200}', value): raise ValueError('Invalid path')
        else: raise ValueError('Unknown nginx operation')
        spec = importlib.util.spec_from_file_location('trusted_nginx', LIB/'nginx.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        asyncio.run(module.apply_reality_sni(value) if kind == 'sni' else module.apply_xhttp_tls_path(value))
        return [0, '', '']
    if operation != 'command': raise ValueError('Unknown operation')
    args = request.get('args')
    if not isinstance(args, list) or not args or not all(isinstance(a, str) and '\0' not in a for a in args): raise ValueError('Invalid arguments')
    command = args[0]
    if command == 'systemctl':
        if len(args) not in {3, 4} or args[1] not in {'is-active', 'start', 'stop', 'restart', 'reload'}: raise ValueError('Forbidden service operation')
        units = args[2:]
        if units[0] == '--quiet': units = units[1:]
        if len(units) != 1 or units[0] not in SERVICES: raise ValueError('Unknown service')
        mutations = {'xray': {'restart'}, 'nginx': {'reload'}, 'warp-svc': {'start', 'stop'}}
        if args[1] != 'is-active' and args[1] not in mutations.get(units[0], set()):
            raise ValueError('Service mutation not required by panel')
        if args[1] in {'start', 'restart'} and units[0] == 'xray':
            data = read_stage(str(STAGE/'xray.json'), 'xray.json');validate_xray(data);write_root(XRAY, data)
        args[0] = '/usr/bin/systemctl'
    elif command == 'xray' and args[1:6] == ['run', '-test', '-format', 'json', '-config'] and len(args) == 7:
        data = read_stage(args[6], 'xray.json.tmp.');validate_xray(data)
        with tempfile.TemporaryDirectory(prefix='md-next-validate-') as directory:
            path = Path(directory)/'config.json';path.write_bytes(data);path.chmod(0o600)
            result = execute(['/usr/local/bin/xray', *args[1:6], str(path)])
            return [result.returncode, result.stdout, result.stderr]
    elif command == 'awg':
        if args[1:] == ['--version']:
            pass
        elif args[1:3] in [['show', 'awg0'], ['showconf', 'awg0']] and len(args) in {3, 4}:
            if len(args) == 4 and args[3] not in {'transfer', 'latest-handshakes', 'allowed-ips'}: raise ValueError('Forbidden AWG query')
        elif args[1:3] == ['syncconf', 'awg0'] and len(args) == 4:
            data = read_stage(args[3], 'awg0.conf.');validate_awg(data)
            with tempfile.TemporaryDirectory(prefix='md-next-awg-sync-') as directory:
                path = Path(directory)/'awg0.conf';path.write_bytes(data);path.chmod(0o600)
                binary = shutil.which('awg', path='/usr/local/bin:/usr/bin') or '/usr/bin/awg'
                result = execute([binary, 'syncconf', 'awg0', str(path)])
                return [result.returncode, result.stdout, 'AWG synchronization failed' if result.returncode else '']
        else: raise ValueError('Forbidden AWG command')
        args[0] = shutil.which('awg', path='/usr/local/bin:/usr/bin') or '/usr/bin/awg'
    elif command == 'awg-quick' and len(args) == 3 and args[1] in {'up', 'strip'} and args[2] == 'awg0':
        data = read_stage(str(STAGE/'awg0.conf'), 'awg0.conf');validate_awg(data);write_root(AWG, data)
        args[0] = shutil.which('awg-quick', path='/usr/local/bin:/usr/bin') or '/usr/bin/awg-quick'
    elif command == 'python3' and len(args) == 4 and Path(args[1]).name == 'awg-routing.py' and args[2] == '--config' and args[3] == str(STAGE/'xray.json'):
        args = ['/usr/bin/python3', str(LIB/'awg-routing.py'), '--config', str(XRAY)]
    elif command == 'systemd-run' and len(args) == 16:
        expected = ['--quiet', '--wait', '--collect', '--pipe', '--unit=md-next-warp-registration',
                    '--property=RuntimeMaxSec=60', '--property=TimeoutStopSec=30', '--property=KillMode=control-group']
        if args[1:9] != expected or args[12] != '--node-id' or not args[13].isdecimal() or args[14:] != ['--config', str(STAGE/'xray.json')]:
            raise ValueError('Invalid WARP registration')
        python = '/opt/md-next/backend/venv/bin/python'
        helper = str(LIB/'warp_registration.py')
        args = ['/usr/bin/systemd-run', *expected, f'--property=ExecStopPost={python} {helper} --cleanup',
                python, helper, '--node-id', args[13], '--config', str(XRAY)]
    else: raise ValueError('Command is not privileged or not supported')
    result = execute(args)
    return [result.returncode, result.stdout, result.stderr]


def main():
    if os.geteuid() != 0: raise RuntimeError('Helper requires root')
    for key in ('MDNEXT_PRIVILEGED_HELPER', 'NGINX_STREAM_CONFIG', 'NGINX_PANEL_CONFIG', 'XRAY_CONFIG_PATH', 'AWG_CONFIG_PATH', 'PYTHONPATH'):
        os.environ.pop(key, None)
    # Trusted modules are installed root-owned, never imported from writable app data.
    sys.path.insert(0, str(LIB))
    payload = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024: raise ValueError('Request too large')
    print(json.dumps(dispatch(json.loads(payload))))


if __name__ == '__main__':
    try: main()
    except Exception:
        print(json.dumps([1, '', 'Privileged operation refused or failed']))
        raise SystemExit(1)
