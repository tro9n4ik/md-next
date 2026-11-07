#!/usr/bin/env python3
"""Выбор AWG 3.1 без удаления ранее установленного рабочего DKMS."""
from pathlib import Path
import subprocess
import sys


def command(*args, check=True):
    return subprocess.run(args, check=check, text=True, timeout=900)


def output(*args, required=False):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if required and result.returncode:
        raise RuntimeError('Не удалось безопасно проверить состояние AWG')
    return result.stdout.strip() if result.returncode == 0 else ''


def package_status(name):
    return output('dpkg-query', '-W', '-f=${Status}', name)


def configure():
    prior_dkms = package_status('amneziawg-dkms')
    kernel = output('uname', '-r')
    try:
        command('apt-get', 'install', '-y', '-qq', '--no-install-recommends', 'linux-headers-' + kernel)
        command('apt-get', 'install', '-y', '-qq', '--no-install-recommends', 'amneziawg-dkms')
        command('dkms', 'autoinstall', '-k', kernel)
        command('modprobe', 'amneziawg')
        version = Path('/sys/module/amneziawg/version').read_text().strip()
        if not version.startswith('3.1.'):
            raise RuntimeError('Модуль ядра не поддерживает AWG 3.1')
        return 'kernel'
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, RuntimeError):
        # На существующем сервере нельзя удалять рабочий пакет или интерфейсы.
        if prior_dkms and prior_dkms != 'deinstall ok config-files':
            raise RuntimeError('Существующий пакет DKMS сохранён; требуется отдельное согласование его замены') from None
        if output('awg', 'show', 'interfaces', required=True):
            raise RuntimeError('Работающие интерфейсы AWG сохранены; автоматическая замена запрещена') from None
        if Path('/sys/module/amneziawg').exists():
            command('modprobe', '-r', 'amneziawg')
        packages = [name for name in ('amneziawg', 'amneziawg-dkms')
                    if package_status(name) not in ('', 'deinstall ok config-files')]
        if packages:
            command('apt-get', '-o', 'APT::Get::AutomaticRemove=false', 'remove', '-y', '-qq', *packages)
        print('Модуль для текущего ядра недоступен; устанавливаем проверенную Go-реализацию AWG 3.1.', flush=True)
        command('bash', str(Path(__file__).with_name('install-awg-userspace.sh')))
        return 'userspace'


if __name__ == '__main__':
    try:
        configure()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
