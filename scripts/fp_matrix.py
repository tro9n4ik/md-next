#!/usr/bin/env python3
"""Compare Chrome/Firefox Reality on the same network without changing subscriptions."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID


def outbound(link, fingerprint):
    parsed = urlsplit(link.strip())
    options = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    if parsed.scheme != 'vless' or options.get('security') != 'reality' or options.get('type', 'tcp') != 'tcp':
        raise ValueError('Нужна ссылка VLESS Reality TCP')
    user = {'id': str(UUID(parsed.username)), 'encryption': 'none'}
    if options.get('flow'): user['flow'] = options['flow']
    if not parsed.hostname or not parsed.port or not options.get('sni') or not options.get('pbk'):
        raise ValueError('Неполная ссылка Reality')
    return {'tag': 'test', 'protocol': 'vless', 'settings': {'vnext': [
        {'address': parsed.hostname, 'port': parsed.port, 'users': [user]}]},
        'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
            'serverName': options['sni'], 'password': options['pbk'],
            'shortId': options.get('sid', ''), 'fingerprint': fingerprint}}}


def probe(link, fingerprint, binary):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    config = {'log': {'loglevel': 'none'}, 'inbounds': [
        {'listen': '127.0.0.1', 'port': port, 'protocol': 'socks', 'settings': {'auth': 'noauth'}}],
        'outbounds': [outbound(link, fingerprint)]}
    with tempfile.TemporaryDirectory(prefix='md-next-fingerprint-') as directory:
        path = Path(directory)/'config.json';path.write_text(json.dumps(config));path.chmod(0o600)
        process = subprocess.Popen([binary, 'run', '-config', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(.5)
            if process.poll() is not None: return {'ok': False, 'error': 'xray_start_failed'}
            result = subprocess.run(['curl', '--noproxy', '', '-fsS', '--max-time', '15',
                '--socks5-hostname', '127.0.0.1:'+str(port), 'https://www.cloudflare.com/cdn-cgi/trace'],
                capture_output=True, text=True, timeout=20)
            trace = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
            return {'ok': result.returncode == 0, 'curl_code': result.returncode,
                    **{key: trace[key] for key in ('ip', 'loc', 'warp') if key in trace}}
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True, help='Файл с одной приватной ссылкой VLESS Reality TCP')
    parser.add_argument('--network', required=True, help='Название проверяемой сети, например mobile или wifi')
    parser.add_argument('--attempts', type=int, default=3)
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 20 or not 0 <= args.interval <= 60: parser.error('Некорректное число проверок или интервал')
    binary = shutil.which('xray')
    if not binary or not shutil.which('curl'): parser.error('Нужны Xray и curl')
    link = args.profile.read_text().strip()
    for attempt in range(args.attempts):
        for fingerprint in ('firefox', 'chrome'):
            result = {'time': datetime.now(timezone.utc).isoformat(), 'network': args.network,
                      'attempt': attempt+1, 'fingerprint': fingerprint, **probe(link, fingerprint, binary)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
        if attempt+1 < args.attempts: time.sleep(args.interval)


if __name__ == '__main__': main()
