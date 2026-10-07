#!/usr/bin/env python3
"""Fail closed for tracked private files and non-placeholder example secrets."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SECRET_FIELDS = {'JWT_SECRET_KEY', 'INITIAL_ADMIN_PASSWORD', 'XRAY_PRIVATE_KEY', 'XRAY_PUBLIC_KEY', 'TELEGRAM_BOT_TOKEN', 'AWG_PRIVATE_KEY', 'WIREGUARD_PRIVATE_KEY'}
PLACEHOLDERS = {'', 'CHANGE_ME', 'YOUR_TELEGRAM_TOKEN', 'YOUR_PRIVATE_KEY', 'YOUR_PUBLIC_KEY'}


def main():
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    failures = []
    for name in filter(None, files):
        path = Path(name)
        if path.name.startswith('.env') and path.name != '.env.example':
            failures.append(name)
        if path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.sql', '.pem', '.key', '.p12', '.pfx', '.mdbackup', '.pcap', '.pcapng'} or any(part in {'.diagnostics', '.runtime'} for part in path.parts):
            failures.append(name)
        if path.name == '.env.example':
            for line in (ROOT / path).read_text(encoding='utf-8').splitlines():
                key, separator, value = line.partition('=')
                if separator and key.strip() in SECRET_FIELDS and value.strip().strip('\"\'') not in PLACEHOLDERS:
                    failures.append(f'{name}: {key.strip()} must be a placeholder')
    if failures:
        print('Private artifacts or example secrets detected:', *sorted(set(failures)), sep='\n', file=sys.stderr)
        return 1
    print('Tracked private files: absent; example secrets: placeholders only')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
