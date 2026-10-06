#!/usr/bin/env python3
"""Install scoped edge defenses on an existing MD-Next server (root, Linux)."""
from pathlib import Path
import datetime
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
CONF = Path('/etc/nginx/sites-available/md-next.conf')
START = '# MD_NEXT_SECURITY_BEGIN'
END = '# MD_NEXT_SECURITY_END'

PUBLIC = '''
    # MD_NEXT_SECURITY_BEGIN
    location ~ ^/(?:wp-login\\.php|xmlrpc\\.php|wp-admin(?:/|$)|phpmyadmin(?:/|$)|\\.env(?:$|/)|\\.git(?:$|/)|actuator(?:/|$)) {
        limit_req zone=md_scanner burst=4 nodelay;
        limit_req zone=md_scanner_total burst=8 nodelay;
        limit_conn md_web_connections 4;
        limit_req_status 429;
        limit_conn_status 429;
        client_max_body_size 16k;
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Forwarded "";
        proxy_set_header CF-Connecting-IP "";
        proxy_set_header True-Client-IP "";
        add_header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" always;
        add_header X-Content-Type-Options nosniff always;
        proxy_connect_timeout 1s;
        proxy_read_timeout 5s;
        proxy_intercept_errors on;
        error_page 500 502 503 504 =404 @md_scanner_unavailable;
    }
    location @md_scanner_unavailable { return 404; }
    location ^~ /_krawl-admin { return 404; }
    # MD_NEXT_SECURITY_END
'''
LOGIN = '''
    # MD_NEXT_SECURITY_BEGIN
    location = /api/v1/auth/login {
        if ($md_panel_banned) { return 403; }
        limit_req zone=md_login burst=8 nodelay;
        limit_conn md_web_connections 8;
        limit_req_status 429;
        limit_conn_status 429;
        client_max_body_size 16k;
        access_log /var/log/nginx/md-next-auth.log md_auth;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
    # MD_NEXT_SECURITY_END
'''

def transform(text):
    import re
    text = re.sub(r'\n\s*# MD_NEXT_SECURITY_BEGIN.*?# MD_NEXT_SECURITY_END\s*\n', '\n', text, flags=re.S)
    if 'root /opt/md-next/backend/app/static/fake/' not in text or 'location /api/' not in text:
        raise ValueError('unsupported nginx layout: no changes applied')
    text = text.replace('    location /api/ {', LOGIN + '\n    location /api/ {', 1)
    root = '    root /opt/md-next/backend/app/static/fake/;'
    text = text.replace(root, root + '\n' + PUBLIC, 1)
    text = text.replace('location / {\n        try_files $uri $uri/ =404;', 'location / {\n        limit_req zone=md_public burst=40 nodelay;\n        limit_conn md_web_connections 20;\n        limit_req_status 429;\n        limit_conn_status 429;\n        add_header Content-Security-Policy "default-src \'none\'; style-src \'unsafe-inline\'; img-src \'self\' data:; frame-ancestors \'none\'; base-uri \'none\'; form-action \'none\'" always;\n        add_header X-Content-Type-Options nosniff always;\n        try_files $uri $uri/ =404;')
    return text

def write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

def main():
    if os.geteuid() != 0:
        raise SystemExit('root required')
    original = CONF.read_text()
    updated = transform(original)
    backup = Path('/root/md-next-security-backups') / datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(CONF, backup / 'md-next.conf')
    static = Path('/opt/md-next/backend/app/static/fake')
    shutil.copytree(static, backup / 'public-site')
    write('/etc/nginx/md-next-panel-bans.map', Path('/etc/nginx/md-next-panel-bans.map').read_text() if Path('/etc/nginx/md-next-panel-bans.map').exists() else '')
    write('/etc/nginx/conf.d/md-next-security.conf', '''map $remote_addr $md_panel_banned { default 0; include /etc/nginx/md-next-panel-bans.map; }
limit_req_zone $binary_remote_addr zone=md_login:1m rate=6r/m;
limit_req_zone $binary_remote_addr zone=md_public:1m rate=15r/s;
limit_req_zone $binary_remote_addr zone=md_scanner:1m rate=2r/s;
limit_req_zone $server_name zone=md_scanner_total:1m rate=4r/s;
limit_conn_zone $binary_remote_addr zone=md_web_connections:1m;
log_format md_auth '$remote_addr [$time_local] $request_method $status';
''')
    CONF.write_text(updated)
    try:
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    except Exception:
        CONF.write_text(original)
        raise
    shutil.copy2(ROOT / 'scripts/panel-ban.py', '/usr/local/sbin/md-next-panel-ban.py')
    write('/etc/fail2ban/filter.d/md-next-panel.conf', '''[Definition]
failregex = ^<HOST> \\[.*\\] POST 401$
ignoreregex =
''')
    write('/etc/fail2ban/action.d/md-next-panel.conf', '''[Definition]
actionban = /usr/bin/python3 /usr/local/sbin/md-next-panel-ban.py ban <ip>
actionunban = /usr/bin/python3 /usr/local/sbin/md-next-panel-ban.py unban <ip>
''')
    peer = os.environ.get('SSH_CONNECTION', '').split(' ')[0]
    import ipaddress
    if peer:
        peer = str(ipaddress.ip_address(peer))
    write('/etc/fail2ban/jail.d/md-next-security.local', f'''[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 {peer}
bantime = 15m
findtime = 10m
maxretry = 6

[sshd]
enabled = true
backend = systemd
banaction = nftables-multiport
port = ssh

[md-next-panel]
enabled = true
backend = polling
filter = md-next-panel
logpath = /var/log/nginx/md-next-auth.log
action = md-next-panel
''')
    write('/etc/logrotate.d/md-next-auth', '''/var/log/nginx/md-next-auth.log {
 daily
 rotate 7
 missingok
 notifempty
 compress
 delaycompress
 create 0640 www-data adm
 sharedscripts
 postrotate
  /usr/sbin/nginx -s reopen
 endscript
}
''')
    subprocess.run(['fail2ban-client', '-t'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'fail2ban'], check=True)
    subprocess.run(['fail2ban-client', 'reload'], check=True)
    for name in ('index.html', 'welcome.txt', 'checklist.txt'):
        source = ROOT / 'backend/app/static/fake' / name
        target = static / name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
    print(f'Security installed; nginx backup: {backup}')

if __name__ == '__main__':
    main()
