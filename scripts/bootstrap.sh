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
curl --fail --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 15 --max-time 180 --retry 3 \
    https://github.com/tro9n4ik/md-next/archive/refs/heads/main.tar.gz \
    -o "$work_dir/source.tar.gz"
tar -xzf "$work_dir/source.tar.gz" -C "$work_dir"
cd "$work_dir/md-next-main"
bash scripts/install.sh </dev/tty
