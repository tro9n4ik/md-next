#!/usr/bin/env python3
"""Политика только для пакетов awg0; основной маршрут сервера не изменяется."""
import argparse
import ipaddress
import json
import subprocess
import time
from pathlib import Path

TUN = 'mdawg'
TAG = 'awg-in'
TABLE = '10086'
PRIORITY = '10086'
CHAIN = 'MDNEXT_AWG_OUT'
STATE = Path('/var/lib/md-next/awg-routing-owned')


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    if check and result.returncode:
        raise RuntimeError(f'Не удалось выполнить {args[0]} {args[1]}: {result.stderr.strip()[:240]}')
    return result


def firewall_rule(*args):
    if run('iptables', '-w', '5', '-C', *args, check=False).returncode:
        run('iptables', '-w', '5', '-I', *args)


def remove():
    # Удаляются только точные правила, которыми управляет MD-Next.
    args = ('FORWARD', '-i', 'awg0', '-j', CHAIN)
    if not run('iptables', '-w', '5', '-C', *args, check=False).returncode:
        run('iptables', '-w', '5', '-D', *args)
    args = ('FORWARD', '-i', TUN, '-o', 'awg0', '-j', 'ACCEPT')
    if not run('iptables', '-w', '5', '-C', *args, check=False).returncode:
        run('iptables', '-w', '5', '-D', *args)
    if not run('iptables', '-w', '5', '-S', CHAIN, check=False).returncode:
        run('iptables', '-w', '5', '-F', CHAIN)
        run('iptables', '-w', '5', '-X', CHAIN)
    run('ip', 'rule', 'del', 'priority', PRIORITY, 'iif', 'awg0', 'lookup', TABLE, check=False)
    # Здесь нет чужих маршрутов: таблица проверяется перед каждым применением.
    run('ip', 'route', 'flush', 'table', TABLE, check=False)


def validate_ownership():
    rules = json.loads(run('ip', '-j', 'rule', 'show').stdout)
    for rule in rules:
        if str(rule.get('priority')) == PRIORITY and not (
            rule.get('iif') == 'awg0' and str(rule.get('table')) == TABLE
        ):
            raise RuntimeError('Приоритет 10086 занят другой политикой; маршрут AWG не изменён')
    routes = run('ip', '-j', 'route', 'show', 'table', TABLE, check=False)
    if routes.returncode == 0:
        for route in json.loads(routes.stdout):
            if not (route.get('dev') in (TUN, 'awg0') or route.get('type') == 'blackhole'):
                raise RuntimeError('Таблица 10086 занята другим маршрутом; маршрут AWG не изменён')


def remove_owned():
    if STATE.exists():
        validate_ownership()
        remove()
        STATE.unlink()


def apply(config_path):
    config = json.loads(Path(config_path).read_text())
    enabled = any(i.get('protocol') == 'tun' and i.get('tag') == TAG
                  and i.get('settings', {}).get('name') == TUN for i in config.get('inbounds', []))
    if not enabled:
        remove_owned()
        return
    validate_ownership()
    if not STATE.exists():
        if not run('iptables', '-w', '5', '-S', CHAIN, check=False).returncode:
            raise RuntimeError('Цепочка MDNEXT_AWG_OUT уже занята; маршрут AWG не изменён')
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text('MD-Next AWG routing v1\n')
        STATE.chmod(0o600)
    if run('iptables', '-w', '5', '-S', CHAIN, check=False).returncode:
        run('iptables', '-w', '5', '-N', CHAIN)
    firewall_rule(CHAIN, '-o', TUN, '-j', 'ACCEPT')
    firewall_rule(CHAIN, '-o', 'awg0', '-j', 'ACCEPT')
    if run('iptables', '-w', '5', '-C', CHAIN, '-j', 'REJECT', check=False).returncode:
        run('iptables', '-w', '5', '-A', CHAIN, '-j', 'REJECT')
    firewall_rule('FORWARD', '-i', 'awg0', '-j', CHAIN)
    firewall_rule('FORWARD', '-i', TUN, '-o', 'awg0', '-j', 'ACCEPT')
    # Запасной blackhole остаётся при исчезновении TUN: нет ухода в main.
    run('ip', 'route', 'replace', 'blackhole', 'default', 'metric', '32760', 'table', TABLE)
    rules = json.loads(run('ip', '-j', 'rule', 'show').stdout)
    if not any(str(r.get('priority')) == PRIORITY for r in rules):
        run('ip', 'rule', 'add', 'priority', PRIORITY, 'iif', 'awg0', 'lookup', TABLE)
    # ExecStartPost может начаться раньше, чем Xray закончит создание интерфейса.
    for _ in range(30):
        if not run('ip', 'link', 'show', TUN, check=False).returncode:
            break
        time.sleep(.1)
    run('ip', 'link', 'set', TUN, 'up')
    addresses = run('ip', '-j', '-4', 'address', 'show', 'dev', 'awg0', check=False)
    if addresses.returncode == 0:
        for device in json.loads(addresses.stdout):
            for address in device.get('addr_info', []):
                subnet = str(ipaddress.ip_network(f"{address['local']}/{address['prefixlen']}", strict=False))
                run('ip', 'route', 'replace', subnet, 'dev', 'awg0', 'table', TABLE)
    run('ip', 'route', 'replace', 'default', 'dev', TUN, 'metric', '10', 'table', TABLE)
    run('sysctl', '-w', 'net.ipv4.ip_forward=1')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/usr/local/etc/xray/config.json')
    parser.add_argument('--remove', action='store_true', help='Remove only routing owned by MD-Next')
    args = parser.parse_args()
    if args.remove:
        remove_owned()
    else:
        apply(args.config)
    print('Маршрут AmneziaWG согласован с Xray')
