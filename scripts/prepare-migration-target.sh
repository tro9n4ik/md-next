#!/bin/bash
# Preflight, OS update and reboot gate precede installation.
set -euo pipefail
if [ "$EUID" -ne 0 ] || [ "$#" -ne 5 ]; then exit 1; fi
exec /usr/bin/python3 "$(dirname "$0")/migration-target.py" "$@"
