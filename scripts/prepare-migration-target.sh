#!/bin/bash
# Run only on a clean target VPS. Does not change DNS or start the imported bot.
set -euo pipefail
if [ "$EUID" -ne 0 ] || [ "$#" -ne 4 ]; then
  echo 'От имени root: bash prepare-migration-target.sh COMMIT MAIN_DOMAIN PANEL_DOMAIN ADMIN_EMAIL' >&2
  exit 1
fi
commit="$1"; main_domain="$2"; panel_domain="$3"; admin_email="$4"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || exit 1
if [ -e /opt/md-next ] || [ -L /opt/md-next ] || [ -e /var/lib/md-next/data/md_next.db ] || systemctl is-active --quiet xray || systemctl is-active --quiet nginx; then
  echo 'На сервере уже есть установка или работающий веб/VPN-сервис. Автоматическая подготовка запрещена.' >&2
  exit 1
fi
. /etc/os-release
case "$ID" in ubuntu|debian) ;; *) echo 'Поддерживаются Debian и Ubuntu'; exit 1;; esac
if ss -H -lnt | awk '{print $4}' | grep -Eq ':(80|443|8000)$'; then
  echo 'Порты 80, 443 или 8000 заняты. Подготовка требует чистого VPS.' >&2
  exit 1
fi
memory_kb=$(awk '/^(MemTotal|SwapTotal):/ {sum += $2} END {print sum}' /proc/meminfo)
[ "$memory_kb" -ge 2097152 ] || { echo 'Для сборки требуется не менее 2 ГБ суммарно RAM и swap'; exit 1; }
available_kb=$(df -Pk /opt | awk 'NR==2 {print $4}')
[ "$available_kb" -ge 5242880 ] || { echo 'Нужно не менее 5 ГБ свободного места'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 ca-certificates
python3 - "$commit" <<'PY'
import json,sys,urllib.request
commit=sys.argv[1]
def api(path):
 request=urllib.request.Request('https://api.github.com/repos/tro9n4ik/md-next/'+path,headers={'User-Agent':'MD-Next-migration'})
 with urllib.request.urlopen(request,timeout=25) as response:return json.load(response)
data=api('commits/'+commit)
assert data['sha']==commit and data['commit']['verification']['verified'], 'Не подтверждена подпись сборки'
runs=api('actions/runs?head_sha='+commit+'&per_page=100')['workflow_runs']
for path in ('.github/workflows/ci.yml','.github/workflows/secrets.yml'):
 matching=[r for r in runs if r['path']==path and r['event']=='push']
 assert matching and max(matching,key=lambda r:r['id'])['conclusion']=='success','Сборка ещё не прошла проверки'
PY
work=$(mktemp -d /root/mdnext-target-XXXXXX)
trap 'rm -rf -- "$work"' EXIT
git clone --quiet --no-checkout https://github.com/tro9n4ik/md-next.git "$work/source"
git -C "$work/source" checkout --quiet --detach "$commit"
if ! printf '%s\n%s\n%s\n' "$main_domain" "$panel_domain" "$admin_email" | MDNEXT_MIGRATION_STAGING=1 MDNEXT_TEST_SELF_SIGNED=1 bash "$work/source/scripts/install.sh" 1; then
  systemctl stop md-next-backend md-next-mtproto xray md-next-awg 2>/dev/null || true
  echo 'Подготовка не завершена. DNS не менялся; проверьте журнал установки.' >&2
  exit 1
fi
echo 'Новый VPS подготовлен в изолированном режиме. Восстановите пробный комплект; DNS пока не переключайте.'
