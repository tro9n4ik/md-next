#!/usr/bin/python3
"""Проверка очистки с настоящим systemd только на одноразовом runner GitHub."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=60, **kwargs)


def main():
    if '--run' not in sys.argv or os.geteuid() != 0 or os.getenv('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Разрешён только явный запуск от root в одноразовом GitHub Actions runner')
    cache, state = Path('/var/cache/md-next'), Path('/var/lib/md-next')
    script = Path(__file__).with_name('disk-maintenance.py')
    for path in (cache, state, Path('/usr/local/lib/md-next/disk-maintenance.py'),
                 Path('/etc/systemd/system/md-next-cache-clean.timer'),
                 Path('/etc/systemd/system/md-next-cache-clean.service')):
        if path.exists() or path.is_symlink():
            raise SystemExit('Проверка не затрагивает существующую установку: '+str(path))
    run(sys.executable, str(script), '--prepare-cache')
    run(sys.executable, str(script), '--install')
    try:
        run('systemctl', 'is-enabled', 'md-next-cache-clean.timer')
        run('systemctl', 'is-active', 'md-next-cache-clean.timer')
        old, fresh = cache/'pip/old-test.whl', cache/'npm/fresh-test.bin'
        outside = state/'database-test.db'
        for path in (old, fresh, outside):
            path.write_text('Демонстрационные данные', encoding='utf-8')
            path.chmod(0o600)
        stamp = time.time()-8*86400
        os.utime(old, (stamp, stamp)); os.utime(outside, (stamp, stamp))
        (cache/'outside-link').symlink_to(outside)
        run('systemctl', 'start', 'md-next-cache-clean.service')
        assert not old.exists() and fresh.exists() and outside.exists()
        assert (cache/'outside-link').is_symlink()
        old.write_text('Кэш активного обновления', encoding='utf-8');old.chmod(0o600)
        os.utime(old, (stamp, stamp))
        with (state/'maintenance.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run('systemctl', 'start', 'md-next-cache-clean.service')
            assert old.exists(), 'Очистка не должна выполняться во время обновления'
        run('systemctl', 'start', 'md-next-cache-clean.service')
        assert not old.exists()
        run('systemctl', 'disable', '--now', 'md-next-cache-clean.timer')
        run(sys.executable, str(script), '--install')
        result = subprocess.run(['systemctl', 'is-enabled', 'md-next-cache-clean.timer'], capture_output=True, text=True, timeout=10)
        assert result.stdout.strip() == 'disabled', 'Повторная установка должна сохранять отключённый таймер'
        print('ОК: настоящий systemd, очистка старых файлов, сохранение данных и ссылок, блокировка обновления и отключённый таймер')
    finally:
        subprocess.run(['systemctl', 'disable', '--now', 'md-next-cache-clean.timer'], capture_output=True, timeout=15)


if __name__ == '__main__':
    main()
