#!/usr/bin/env python3
"""Fail2ban action confined to the panel login location; never firewall VPN."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

STATE = Path('/var/lib/md-next-security/panel-bans.json')
MAP = Path('/etc/nginx/md-next-panel-bans.map')
NGINX_TEST_TIMEOUT = 10
NGINX_RELOAD_TIMEOUT = 20

def main():
    operation, address = sys.argv[1:]
    address = str(ipaddress.ip_address(address))
    if operation not in ('ban', 'unban'):
        raise ValueError('unsupported action')
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE.parent, 0o700)
    with (STATE.parent / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        banned = set(json.loads(STATE.read_text())) if STATE.exists() else set()
        banned.add(address) if operation == 'ban' else banned.discard(address)
        previous = MAP.read_text() if MAP.exists() else ''
        candidate = ''.join(f'{ip} 1;\n' for ip in sorted(banned))
        temporary = MAP.with_suffix('.tmp')
        temporary.write_text(candidate)
        os.replace(temporary, MAP)
        reload = ['systemctl', 'reload', 'nginx']
        try:
            subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=NGINX_TEST_TIMEOUT)
            subprocess.run(reload, check=True, timeout=NGINX_RELOAD_TIMEOUT)
        except Exception as error:
            temporary.write_text(previous)
            os.replace(temporary, MAP)
            if isinstance(error, subprocess.TimeoutExpired):
                if error.cmd == reload:
                    try:
                        subprocess.run(reload, check=True, timeout=NGINX_RELOAD_TIMEOUT)
                    except (OSError, subprocess.SubprocessError):
                        raise RuntimeError('Время ожидания Nginx истекло. Файл восстановлен, применение не подтверждено.') from None
                raise RuntimeError('Время ожидания Nginx истекло. Файл блокировок восстановлен.') from None
            raise
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(sorted(banned)))
        os.replace(temporary, STATE)

if __name__ == '__main__':
    main()
