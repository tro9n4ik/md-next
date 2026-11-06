#!/usr/bin/env python3
"""Raise Nginx connection limits without lowering administrator settings."""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def render(config, service_limit=0):
    events = re.search(r'(?m)^\s*events\s*\{[^{}]*\}', config)
    if events is None:
        raise ValueError('Не найден стандартный блок events Nginx')
    body = events.group()
    matches = list(re.finditer(r'(?m)^(\s*)worker_connections\s+(\d+)\s*;', body))
    if len(matches) > 1:
        raise ValueError('Несколько директив worker_connections')
    connections = max(8192, int(matches[0][2]) if matches else 0)
    if matches:
        body = re.sub(r'(?m)^(\s*)worker_connections\s+\d+\s*;',
                      lambda m: f'{m[1]}worker_connections {connections};', body)
    else:
        body = body[:-1] + f'    worker_connections {connections};\n}}'
    config = config[:events.start()] + body + config[events.end():]
    limits = list(re.finditer(r'(?m)^(\s*)worker_rlimit_nofile\s+(\d+)\s*;', config))
    if len(limits) > 1:
        raise ValueError('Несколько директив worker_rlimit_nofile')
    nofile = max(65536, connections * 2, int(limits[0][2]) if limits else 0)
    if limits:
        config = re.sub(r'(?m)^(\s*)worker_rlimit_nofile\s+\d+\s*;',
                        lambda m: f'{m[1]}worker_rlimit_nofile {nofile};', config)
    else:
        config = f'worker_rlimit_nofile {nofile};\n' + config
    service = max(nofile, service_limit)
    return config, f'[Service]\nLimitNOFILE={service}\n'


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def configure(root=Path('/')):
    config = root/'etc/nginx/nginx.conf'
    dropin = root/'etc/systemd/system/nginx.service.d/50-md-next-capacity.conf'
    original = config.read_bytes()
    previous = dropin.read_bytes() if dropin.exists() else None
    value = run('systemctl', 'show', 'nginx', '-p', 'LimitNOFILE', '--value').stdout.strip()
    service_limit = int(value) if value.isdecimal() else 2**20 if value == 'infinity' else 0
    updated, service = render(original.decode(), service_limit)
    if value == 'infinity':
        service = '[Service]\nLimitNOFILE=infinity\n'
    if original == updated.encode() and previous == service.encode():
        run('nginx', '-t')
        return
    backup = root/('root/md-next-nginx-capacity-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(mode=0o700, parents=True)
    shutil.copy2(config, backup/'nginx.conf')
    if previous is not None:
        shutil.copy2(dropin, backup/'capacity.conf')
    try:
        config.write_text(updated)
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(service)
        dropin.chmod(0o644)
        run('nginx', '-t')
        run('systemctl', 'daemon-reload')
        # Reload preserves established VPN streams; the service limit also
        # applies to future starts. The root master sets the worker rlimit.
        run('systemctl', 'reload', 'nginx')
    except Exception:
        config.write_bytes(original)
        if previous is None:
            dropin.unlink(missing_ok=True)
        else:
            dropin.write_bytes(previous)
        run('systemctl', 'daemon-reload')
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        raise


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Требуется root')
    configure()
