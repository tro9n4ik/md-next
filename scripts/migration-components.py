"""Подготовка дополнительных компонентов чистой целевой установки."""
import hashlib
import io
import json
import platform
import re
import subprocess
import tarfile
import urllib.request
from pathlib import PurePosixPath


def adguard_config(blob):
    import yaml
    config=yaml.safe_load(blob)
    if not isinstance(config,dict) or not isinstance(config.get('dns'),dict):
        raise ValueError('Некорректная конфигурация AdGuard Home')
    if config.get('dhcp',{}).get('enabled') or config.get('tls',{}).get('enabled'):
        raise ValueError('Для отдельного TLS или DHCP AdGuard требуется ручная подготовка')
    config.setdefault('http',{})['address']='127.0.0.1:3001'
    config['dns']['bind_hosts']=['127.0.0.1']
    config['dns']['port']=5354
    return yaml.safe_dump(config,allow_unicode=True).encode()


def request_bytes(url,limit):
    request=urllib.request.Request(url,headers={'User-Agent':'MD-Next-migration'})
    with urllib.request.urlopen(request,timeout=60) as response:data=response.read(limit+1)
    if len(data)>limit:raise ValueError('Превышен размер компонента')
    return data


def adguard_binary(version):
    if not re.fullmatch(r'v\d+\.\d+\.\d+',version):raise ValueError('Неизвестная версия AdGuard')
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
    if not arch:raise ValueError('Неподдерживаемая архитектура AdGuard')
    release=json.loads(request_bytes('https://api.github.com/repos/AdguardTeam/AdGuardHome/releases/tags/'+version,1024*1024))
    asset=next(a for a in release['assets'] if a['name']==f'AdGuardHome_linux_{arch}.tar.gz')
    digest=asset.get('digest','')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):raise ValueError('Нет подтверждённой контрольной суммы AdGuard')
    url=asset['browser_download_url']
    if url!=f'https://github.com/AdguardTeam/AdGuardHome/releases/download/{version}/AdGuardHome_linux_{arch}.tar.gz':
        raise ValueError('Неожиданный адрес компонента')
    data=request_bytes(url,64*1024*1024)
    if 'sha256:'+hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Контрольная сумма AdGuard не совпадает')
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
        candidates=[m for m in archive.getmembers() if PurePosixPath(m.name).as_posix()=='AdGuardHome/AdGuardHome']
        if len(candidates)!=1:raise ValueError('Неоднозначный состав архива AdGuard')
        member=candidates[0]
        if not member.isfile() or not 0<member.size<=128*1024*1024:raise ValueError('Некорректный архив AdGuard')
        return archive.extractfile(member).read()


UNIT='''[Unit]
Description=MD-Next AdGuard Home
After=network-online.target
[Service]
User=md-next-adguard
Group=md-next-adguard
ExecStart=/opt/md-next-adguard/AdGuardHome -c /var/lib/md-next-adguard/AdGuardHome.yaml -w /var/lib/md-next-adguard --no-check-update
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/md-next-adguard
[Install]
WantedBy=multi-user.target
'''


def prepare_adguard(manifest,root,write,command):
    component=manifest.get('components',{}).get('adguard')
    if not component:return
    state=root/'var/lib/md-next-adguard'
    config=adguard_config((state/'AdGuardHome.yaml').read_bytes())
    marker=root/'var/lib/md-next/migration/adguard-owned'
    binary_path=root/'opt/md-next-adguard/AdGuardHome'
    binary=None
    try:
        saved=json.loads(marker.read_text())
        if saved.get('version')==component['version'] and binary_path.is_file() and not binary_path.is_symlink() and binary_path.stat().st_size<=128*1024*1024:
            existing=binary_path.read_bytes()
            if hashlib.sha256(existing).hexdigest()==saved.get('sha256'):binary=existing
    except (OSError,ValueError,TypeError,AttributeError):pass
    if binary is None:binary=adguard_binary(component['version'])
    command('systemctl','stop','md-next-adguard',check=False)
    if command('id','-u','md-next-adguard',check=False).returncode:
        command('useradd','--system','--user-group','--home-dir','/var/lib/md-next-adguard','--shell','/usr/sbin/nologin','md-next-adguard')
    write(root/'opt/md-next-adguard/AdGuardHome',binary,0o755)
    write(marker,json.dumps({'version':component['version'],'sha256':hashlib.sha256(binary).hexdigest()}).encode(),0o600)
    write(state/'AdGuardHome.yaml',config,0o600)
    write(root/'etc/systemd/system/md-next-adguard.service',UNIT.encode(),0o644)
    command('chown','-R','md-next-adguard:md-next-adguard',str(state))
    command('systemctl','daemon-reload')
    command('systemctl','start','md-next-adguard')
