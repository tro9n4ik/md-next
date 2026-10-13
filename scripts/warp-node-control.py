#!/usr/bin/python3 -I
"""Loopback-only WARP controls carried inside the encrypted node channel."""
import hmac
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path('/etc/md-next-warp')
PUBLIC_PORT = 40000
UPSTREAM_PORT = 40001


def command(args, timeout=15):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Системная операция WARP не выполнена')
    return result.stdout


def trace(ipv4=True):
    args = ['curl', '-fsS', '--max-time', '5']
    args += ['--socks5', '127.0.0.1:40001', '-4'] if ipv4 else ['--socks5-hostname', '127.0.0.1:40001']
    output = command(args + [('https://1.1.1.1/cdn-cgi/trace' if ipv4 else 'https://www.cloudflare.com/cdn-cgi/trace')])
    values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    address = ipaddress.ip_address(values.get('ip', ''))
    if values.get('warp') not in ('on', 'plus') or (ipv4 and address.version != 4):
        raise RuntimeError('Выход WARP не прошёл проверку')
    return {'ip': str(address), 'country': values.get('loc', ''), 'warp': values['warp']}


def rotate(ipv4=True):
    before = trace(ipv4)
    try:
        command(['systemctl', 'restart', 'warp-svc'])
        time.sleep(2)
        # The daemon restores the persisted connection automatically on startup.
        for attempt in range(4):
            try:
                after = trace(ipv4)
                return {'old_ip': before['ip'], 'new_ip': after['ip'],
                        'changed': before['ip'] != after['ip'], **after}
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 3:
                    raise
                time.sleep(1)
    except Exception:
        # Keep the same registration; restore connectivity rather than destroy identity.
        command(['systemctl', 'restart', 'warp-svc'])
        for attempt in range(4):
            try:
                trace(ipv4)
                break
            except (RuntimeError, subprocess.TimeoutExpired):
                if attempt == 3:
                    raise RuntimeError('Не удалось восстановить выход WARP')
                time.sleep(1)
        raise


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.connection.settimeout(5)
        secret = (ROOT / 'secret').read_text().strip()
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + secret):
            self.reply(401, {'error': 'Нет доступа'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {'ipv4_only'} or type(data['ipv4_only']) is not bool:
                raise ValueError()
        except (ValueError, OSError):
            self.reply(400, {'error': 'Некорректный запрос'})
            return
        try:
            if self.path == '/status':
                result = {'version': 1, 'ipv4_port': PUBLIC_PORT, 'dual_port': UPSTREAM_PORT}
            elif self.path == '/rotate':
                result = rotate(data['ipv4_only'])
            else:
                self.reply(404, {'error': 'Неизвестная операция'})
                return
            self.reply(200, result)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            self.reply(502, {'error': 'WARP не прошёл проверку после переподключения. Регистрация сохранена; повторите проверку.'})


def _install():
    if os.geteuid() != 0:
        raise RuntimeError('Установку выполняет root')
    secret = os.environ.get('NODE_SECRET', '')
    if len(secret) < 16 or '\n' in secret:
        raise ValueError('Секрет ноды не задан')
    binary = shutil.which('xray')
    if not binary:
        raise RuntimeError('Xray не установлен')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    ROOT.chmod(0o700)
    config = {'log': {'loglevel': 'warning'}, 'dns': {'servers': ['localhost'], 'queryStrategy': 'UseIPv4'},
              'inbounds': [{'listen': '127.0.0.1', 'port': PUBLIC_PORT, 'protocol': 'socks', 'settings': {'auth': 'noauth', 'udp': True},
                            'sniffing': {'enabled': True, 'destOverride': ['http', 'tls', 'quic'], 'routeOnly': False}}],
              'routing': {'rules': [{'type': 'field', 'ip': ['::/0'], 'outboundTag': 'block-ipv6'}]},
              'outbounds': [{'tag': 'ipv4', 'protocol': 'freedom', 'settings': {'domainStrategy': 'UseIPv4'},
                             'streamSettings': {'sockopt': {'dialerProxy': 'warp-upstream'}}},
                            {'tag': 'warp-upstream', 'protocol': 'socks', 'settings': {'servers': [{'address': '127.0.0.1', 'port': UPSTREAM_PORT}]}},
                            {'tag': 'block-ipv6', 'protocol': 'blackhole'}]}
    path = ROOT / 'ipv4.json'
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    command([binary, 'run', '-test', '-config', str(path)])
    (ROOT / 'secret').write_text(secret)
    (ROOT / 'secret').chmod(0o600)
    destination = Path('/usr/local/lib/md-next-warp-control.py')
    if Path(__file__).resolve() != destination:
        shutil.copyfile(__file__, destination)
    destination.chmod(0o700)
    units = {'md-next-warp-ipv4': binary + ' run -config ' + str(path),
             'md-next-warp-control': '/usr/bin/python3 -I ' + str(destination)}
    for name, start in units.items():
        Path('/etc/systemd/system/' + name + '.service').write_text(
            '[Unit]\nAfter=network-online.target warp-svc.service\nWants=warp-svc.service\n'
            '[Service]\nExecStart=' + start + '\nRestart=on-failure\nRestartSec=3\n'
            'NoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nPrivateTmp=yes\n'
            '[Install]\nWantedBy=multi-user.target\n')
    command(['warp-cli', '--accept-tos', 'proxy', 'port', str(UPSTREAM_PORT)])
    command(['systemctl', 'daemon-reload'])
    for name in units:
        command(['systemctl', 'enable', '--now', name])
        command(['systemctl', 'restart', name])
    trace()


def install():
    paths = [ROOT / 'ipv4.json', ROOT / 'secret', Path('/usr/local/lib/md-next-warp-control.py'),
             Path('/etc/systemd/system/md-next-warp-ipv4.service'),
             Path('/etc/systemd/system/md-next-warp-control.service')]
    previous = {p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None for p in paths}
    units = ['md-next-warp-ipv4', 'md-next-warp-control']
    enabled = {u: subprocess.run(['systemctl', 'is-enabled', u], capture_output=True).returncode == 0 for u in units}
    active = {u: subprocess.run(['systemctl', 'is-active', u], capture_output=True).returncode == 0 for u in units}
    # An existing IPv4 adapter uses the upstream port; a fresh WARP uses 40000.
    old_port = UPSTREAM_PORT if active['md-next-warp-ipv4'] else PUBLIC_PORT
    try:
        _install()
    except Exception:
        for unit in units:
            subprocess.run(['systemctl', 'disable', '--now', unit], capture_output=True)
        for path, saved in previous.items():
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(saved[0])
                path.chmod(saved[1])
        subprocess.run(['warp-cli', '--accept-tos', 'proxy', 'port', str(old_port)], capture_output=True)
        subprocess.run(['systemctl', 'daemon-reload'], capture_output=True)
        for unit in units:
            if enabled[unit]:
                subprocess.run(['systemctl', 'enable', unit], capture_output=True)
            if active[unit]:
                subprocess.run(['systemctl', 'start', unit], capture_output=True)
        raise


if __name__ == '__main__':
    if sys.argv[1:] == ['--install']:
        install()
    elif not sys.argv[1:]:
        HTTPServer(('127.0.0.1', 40002), Handler).serve_forever()
    else:
        raise SystemExit('Неизвестная операция')
