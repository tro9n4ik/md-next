"""Постоянные службы и порядок загрузки панели; без перезапуска клиентов."""
from pathlib import Path
import subprocess

AWG='''[Unit]
Description=MD-Next AmneziaWG and Xray routing
Wants=network-online.target xray.service
After=network-online.target xray.service
ConditionPathExists=/var/lib/md-next/awg-boot-enabled
ConditionPathExists=/etc/amnezia/amneziawg/awg0.conf
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/lib/md-next/awg-boot.sh
ExecStartPost=/usr/bin/python3 /usr/local/lib/md-next/awg-routing.py --config /usr/local/etc/xray/config.json
ExecStop=/usr/bin/awg-quick down awg0
TimeoutStartSec=90
[Install]
WantedBy=multi-user.target
'''
BOOT='''#!/bin/sh
set -eu
if /usr/sbin/ip link show dev awg0 >/dev/null 2>&1; then
    /usr/bin/awg show awg0 >/dev/null
else
    /usr/bin/awg-quick up awg0
fi
'''


def configure(root=Path('/'),run=subprocess.run):
    def write(name,data,mode=0o644):
        path=root/name
        if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Ссылка в системном пути')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data);path.chmod(mode)
    env=(root/'opt/md-next/backend/.env').read_text()
    active='MDNEXT_MIGRATION_STAGING=1' not in env.splitlines()
    marker=root/'var/lib/md-next/awg-boot-enabled'
    if active:write('var/lib/md-next/awg-boot-enabled','1',0o600)
    else:marker.unlink(missing_ok=True)
    write('usr/local/lib/md-next/awg-boot.sh',BOOT,0o700)
    write('etc/systemd/system/md-next-awg.service',AWG)
    present=[]
    dependencies=['network-online.target','nginx.service']
    if active:dependencies.append('xray.service')
    for unit in ('warp-svc','md-next-adguard','md-next-mtproto'):
        result=run(['systemctl','show',unit,'-p','LoadState','--value'],capture_output=True,text=True,check=False)
        if result.returncode==0 and result.stdout.strip()=='loaded':
            selected=(run(['systemctl','is-enabled','--quiet',unit],check=False,capture_output=True).returncode==0 or
                      run(['systemctl','is-active','--quiet',unit],check=False,capture_output=True).returncode==0)
            if active and selected:
                present.append(unit);dependencies.append(unit+'.service')
    if active:dependencies.append('md-next-awg.service')
    write('etc/systemd/system/md-next-backend.service.d/40-md-next-boot-order.conf',
          '[Unit]\nWants='+ ' '.join(dependencies)+'\nAfter='+' '.join(dependencies)+'\n')
    upstream=[u for u in dependencies if u in ('network-online.target','warp-svc.service','md-next-adguard.service')]
    write('etc/systemd/system/xray.service.d/40-md-next-boot-order.conf',
          '[Unit]\nWants='+' '.join(upstream)+'\nAfter='+' '.join(upstream)+'\n')
    write('etc/sysctl.d/90-md-next-awg.conf','net.ipv4.ip_forward=1\n')
    run(['systemctl','daemon-reload'],check=True)
    if active:
        run(['systemctl','enable','nginx','xray','md-next-backend','md-next-awg',*present],check=True)
    else:
        for unit in ('xray','md-next-awg','md-next-mtproto'):
            run(['systemctl','disable',unit],check=False,capture_output=True)


if __name__=='__main__':configure()
