#!/usr/bin/env bash
# First-party bootstrap. Interactive installation continues from the terminal.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
    echo 'Запустите установщик через sudo bash.' >&2
    exit 1
fi
command -v curl >/dev/null || { echo 'Сначала установите curl.' >&2; exit 1; }
command -v tar >/dev/null || { echo 'Сначала установите tar.' >&2; exit 1; }
[[ -r /dev/tty ]] || { echo 'Для установки нужен интерактивный терминал.' >&2; exit 1; }
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
# Сначала закрепляем точный коммит: архив main не содержит Git-историю.
revision=$(curl --fail --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 15 --max-time 30 --retry 3 \
    -H 'Accept: application/vnd.github.sha' -H 'User-Agent: MD-Next-bootstrap' \
    https://api.github.com/repos/tro9n4ik/md-next/commits/main)
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Не удалось определить точную сборку GitHub.' >&2; exit 1; }
curl --fail --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 15 --max-time 180 --retry 3 \
    "https://github.com/tro9n4ik/md-next/archive/$revision.tar.gz" \
    -o "$work_dir/source.tar.gz"
tar -xzf "$work_dir/source.tar.gz" -C "$work_dir"
cd "$work_dir/md-next-$revision"
MDNEXT_INSTALL_COMMIT="$revision" bash scripts/install.sh </dev/tty
