#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PIN=37dce9a535d84d1707c18d6c8f4b68b2ee5d8adf
DEST=/opt/md-next-krawl
if ! id mdnext-krawl >/dev/null 2>&1; then useradd --system --no-create-home --shell /usr/sbin/nologin mdnext-krawl; fi
install -d -m 0750 -o mdnext-krawl -g mdnext-krawl /var/lib/md-next-krawl /var/lib/md-next-krawl/data /var/lib/md-next-krawl/logs
install -d -m 0750 -o root -g mdnext-krawl /etc/md-next-krawl
if [ ! -d "$DEST/.git" ]; then git clone --no-checkout https://github.com/BlessedRebuS/Krawl.git "$DEST"; fi
git -C "$DEST" fetch origin "$PIN"
git -C "$DEST" checkout --detach "$PIN"
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --disable-pip-version-check -r "$SOURCE_DIR/scripts/krawl-requirements.lock"
"$DEST/venv/bin/pip" freeze > /etc/md-next-krawl/installed-requirements.txt
install -m 0644 "$SOURCE_DIR/scripts/krawl-observer.py" "$DEST/src/mdnext_observer.py"
install -m 0644 "$SOURCE_DIR/scripts/krawl-resource-guard.py" /usr/local/sbin/md-next-krawl-resource-guard.py
install -m 0644 "$SOURCE_DIR/backend/app/static/fake/index.html" /etc/md-next-krawl/public-template.html
if [ ! -e /etc/md-next-krawl/environment ]; then
    umask 027
    printf 'KRAWL_DASHBOARD_PASSWORD=%s\n' "$(openssl rand -hex 32)" > /etc/md-next-krawl/environment
    chown root:mdnext-krawl /etc/md-next-krawl/environment
fi
cat > /etc/md-next-krawl/config.yaml <<'YAML'
mode: standalone
server: {port: 5001, delay: 0}
links: {min_per_page: 0, max_per_page: 0, max_counter: 1}
dashboard: {secret_path: /_krawl-admin, cache_warmup: false, warmup_aggregation: false}
database: {path: /var/lib/md-next-krawl/data/krawl.db, retention_days: 7, persist_suspicious_only: true}
crawl: {infinite_pages_for_malicious: false, max_pages_limit: 1000000000, ban_duration_seconds: 0}
tarpit: {enabled: false}
ai: {enabled: false, max_daily_requests: 0}
deception: {import_pages: false}
banlist: {export_path: '', sources: []}
backups: {enabled: false}
metrics: {enabled: false}
analyzer: {tlsh_enabled: false}
logging: {level: INFO}
custom_template_path: /etc/md-next-krawl/public-template.html
YAML
chown root:mdnext-krawl /etc/md-next-krawl/config.yaml
chmod 0640 /etc/md-next-krawl/config.yaml
cat > /etc/systemd/system/md-next-krawl.service <<'UNIT'
[Unit]
Description=MD-Next isolated Krawl observer
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
User=mdnext-krawl
Group=mdnext-krawl
WorkingDirectory=/var/lib/md-next-krawl
Environment=CONFIG_LOCATION=/etc/md-next-krawl/config.yaml
EnvironmentFile=/etc/md-next-krawl/environment
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=KRAWL__SERVER_IP_RESOLVED=true
ExecStart=/opt/md-next-krawl/venv/bin/uvicorn --app-dir /opt/md-next-krawl/src mdnext_observer:app --host 127.0.0.1 --port 5001 --workers 1 --limit-concurrency 12 --backlog 32 --timeout-keep-alive 3 --no-server-header --no-access-log --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=15
MemoryHigh=96M
MemoryMax=128M
CPUQuota=20%
TasksMax=32
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
ReadWritePaths=/var/lib/md-next-krawl
UMask=0027

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
cat > /etc/systemd/system/md-next-krawl-guard.service <<'UNIT'
[Unit]
Description=Check isolated observer disk usage
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/sbin/md-next-krawl-resource-guard.py
MemoryMax=32M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
UNIT
cat > /etc/systemd/system/md-next-krawl-guard.timer <<'UNIT'
[Unit]
Description=Bound Krawl observer disk usage
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl reset-failed md-next-krawl
systemctl enable --now md-next-krawl
systemctl restart md-next-krawl
systemctl enable --now md-next-krawl-guard.timer
for attempt in {1..30}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:5001/_krawl-admin/healthz >/dev/null; then
        echo 'Krawl health check passed'
        exit 0
    fi
    sleep 1
done
echo 'Krawl health check failed; inspect journalctl -u md-next-krawl' >&2
exit 1
