#!/bin/bash
# Полностью получаю установщик до запуска; SSH не является владельцем задания.
set -eu
umask 077
if [ "$(id -u)" -ne 0 ]; then echo 'Запустите команду от root.' >&2; exit 1; fi
for tool in systemd-run systemctl flock base64 sha256sum; do
    command -v "$tool" >/dev/null || { echo "Для автономной установки требуется $tool." >&2; exit 1; }
done
if [ ! -d /run/systemd/system ]; then echo 'Требуется запущенный systemd.' >&2; exit 1; fi
root=/run/md-next-node-installer
if [ -L "$root" ]; then echo 'Небезопасный каталог установщика.' >&2; exit 1; fi
mkdir -p "$root"
if [ "$(stat -c %u "$root")" != 0 ]; then echo 'Каталог установщика принадлежит другому пользователю.' >&2; exit 1; fi
chmod 700 "$root"
exec 9>"$root/lock"
flock -n 9 || { echo 'Другая команда установки уже запускается.' >&2; exit 1; }
unit=md-next-node-install.service
state=$(systemctl show "$unit" --property=SubState --value 2>/dev/null || true)
case "$state" in running|start*|stop*) echo 'Установка уже выполняется. Журнал: journalctl -u md-next-node-install -f' >&2; exit 1;; esac
job=$(mktemp -d "$root/job.XXXXXXXX")
trap 'rm -f -- "$job/install.sh"; rmdir -- "$job" 2>/dev/null || true' EXIT
base64 -d >"$job/install.sh" <<'MDNEXT_PAYLOAD'
__INSTALLER_BASE64__
MDNEXT_PAYLOAD
printf '%s  %s\n' '__INSTALLER_SHA256__' "$job/install.sh" | sha256sum -c - >/dev/null
# Токен находится только в закрытом файле, а не в аргументах процессов/unit.
systemctl stop "$unit" 2>/dev/null || true
systemctl reset-failed "$unit" 2>/dev/null || true
systemd-run --unit=md-next-node-install --description='Установка ноды MD-Next' \
    --property=Type=exec --property=RemainAfterExit=yes --property=RuntimeMaxSec=1800 \
    --property=TimeoutStopSec=15 --property=KillMode=control-group --property=UMask=0077 \
    --property="ExecStopPost=/bin/rm -f $job/install.sh" \
    --setenv="MD_NEXT_NODE_LOG_FILE=$root/details.log" \
    --setenv="MD_NEXT_NODE_WARP=${MD_NEXT_NODE_WARP:-1}" \
    /bin/bash "$job/install.sh" </dev/null
trap - EXIT
echo 'Установка запущена отдельной службой. Можно закрыть SSH.'
echo 'Ход установки: journalctl -u md-next-node-install -f'
echo 'Результат: systemctl status md-next-node-install --no-pager'
echo "Подробный журнал: $root/details.log"
echo 'Обрыв SSH не останавливает задание; перезагрузка сервера остановит его.'
