#!/bin/bash
# Destructive smoke test. Run only in a disposable, initially clean VM.
set -euo pipefail
if [ "${MDNEXT_DISPOSABLE_VM:-0}" != "1" ] || [ "$EUID" -ne 0 ]; then
  echo "Нужна отдельная одноразовая VM, root и MDNEXT_DISPOSABLE_VM=1." >&2
  exit 1
fi
if [ -e /opt/md-next ] || [ -e /etc/systemd/system/md-next-backend.service ]; then
  echo "Обнаружена существующая установка: тест отменён." >&2
  exit 1
fi
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TEST_LOG="$(mktemp)"
trap 'if [ "$?" -ne 0 ]; then tail -60 "$TEST_LOG" | sed -E "s/(Пароль:|INITIAL_ADMIN_PASSWORD=|JWT_SECRET_KEY=).*/\1 [скрыто]/"; fi; rm -f "$TEST_LOG"' EXIT
export MDNEXT_TEST_SELF_SIGNED=1
bash "$SCRIPT_DIR/install.sh" > "$TEST_LOG" 2>&1 <<'INPUT'
1
md-next.test
panel.md-next.test
admin@example.com
INPUT
systemctl is-active --quiet md-next-backend
systemctl is-active --quiet xray
systemctl is-active --quiet nginx
awg show awg0 > /dev/null
nginx -t
test "$(curl -ksS --resolve panel.md-next.test:443:127.0.0.1 https://panel.md-next.test/ -o /dev/null -w '%{http_code}')" = "200"
cd /opt/md-next/backend
venv/bin/python - <<'PY'
import json
import urllib.request
from dotenv import load_dotenv
import os
load_dotenv('.env')
request = urllib.request.Request('http://127.0.0.1:8000/api/v1/auth/login', data=json.dumps({'username':'admin','password':os.environ['INITIAL_ADMIN_PASSWORD']}).encode(), headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request) as response:
    assert response.status == 200
    assert json.load(response)['access_token']
from app.services.backups import create_backup
assert create_backup('SmokeTestBackupPassword123!')
PY
test -f /etc/sysctl.d/90-md-next-awg.conf
ip rule show | grep -q '^10086:'
iptables -S MDNEXT_AWG_OUT > /dev/null
test -f /var/lib/md-next/awg-routing-owned
test -n "$(find backups -name '*.mdbackup' -print -quit)"
ENV_HASH="$(sha256sum .env)"
# A foreign policy must survive both forms of removal.
ip rule add priority 12345 lookup 12345
ip route add blackhole default table 12345
printf 'ОТМЕНА\n' | bash "$SCRIPT_DIR/uninstall.sh" >> "$TEST_LOG" 2>&1
systemctl is-active --quiet md-next-backend
printf 'УДАЛИТЬ\nn\n' | bash "$SCRIPT_DIR/uninstall.sh" >> "$TEST_LOG" 2>&1
if systemctl is-active --quiet md-next-backend; then exit 1; fi
if systemctl is-active --quiet xray; then exit 1; fi
if ip link show awg0 >/dev/null 2>&1; then exit 1; fi
test -f md_next.db
test "$(sha256sum .env)" = "$ENV_HASH"
test -n "$(find backups -name '*.mdbackup' -print -quit)"
if ip rule show | grep -q '^10086:'; then exit 1; fi
if iptables -S MDNEXT_AWG_OUT 2>/dev/null; then exit 1; fi
test ! -e /etc/sysctl.d/90-md-next-awg.conf
test ! -e /etc/systemd/system/xray.service.d/30-md-next-awg-routing.conf
ip rule show | grep -q '^12345:'
printf 'УДАЛИТЬ\ny\n' | bash "$SCRIPT_DIR/uninstall.sh" >> "$TEST_LOG" 2>&1
test ! -e /opt/md-next
test ! -e /var/lib/md-next
test ! -e /usr/local/etc/xray/config.json
test ! -e /etc/amnezia/amneziawg/awg0.conf
test ! -e /etc/letsencrypt/renewal-hooks/deploy/md-next-xray-certificate.sh
ip rule show | grep -q '^12345:'
ip route show table 12345 | grep -q blackhole
echo "Установка, вход, копия, отмена, сохранение данных и полное удаление проверены."
