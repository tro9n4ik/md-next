#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$(id -u)" -ne 0 ]; then echo 'root required' >&2; exit 1; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y fail2ban python3-venv git build-essential curl
bash "$ROOT/scripts/install-krawl.sh"
python3 "$ROOT/scripts/install-security.py"
