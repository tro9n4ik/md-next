#!/usr/bin/env python3
"""Stop only the observer if its state grows or disk reserve gets too small."""
from pathlib import Path
import shutil
import subprocess
import sys

OBSERVER_STOP_TIMEOUT = 20

def exceeded(root, maximum=96 * 1024 * 1024, minimum_free=512 * 1024 * 1024):
    used = sum(path.lstat().st_size for path in root.rglob('*') if path.is_file() and not path.is_symlink())
    return used > maximum or shutil.disk_usage(root).free < minimum_free

if __name__ == '__main__':
    root = Path('/var/lib/md-next-krawl')
    if exceeded(root):
        try:
            subprocess.run(['systemctl', 'stop', 'md-next-krawl'], check=True, timeout=OBSERVER_STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            print('Время ожидания остановки наблюдателя истекло.', file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        print('Krawl observer stopped: state or free-disk threshold exceeded. VPN services unaffected.', flush=True)
        raise SystemExit(1)
