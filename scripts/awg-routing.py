#!/usr/bin/env python3
"""Политика только для пакетов awg0; основной маршрут сервера не изменяется."""
import argparse
import ipaddress
import json
import subprocess
import time
import importlib.util
import os
import stat
import shutil
from contextlib import contextmanager
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl = None

TUN = 'mdawg'
TAG = 'awg-in'
TABLE = '10086'
PRIORITY = '10086'
CHAIN = 'MDNEXT_AWG_OUT'
STATE = Path('/var/lib/md-next/awg-routing-owned')
spec = importlib.util.spec_from_file_location("network_mtu", Path(__file__).with_name("network-mtu.py"))
mtu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mtu)
MSS_RULES = tuple(
    ('FORWARD', '-i', ingress, '-o', egress, '-p', 'tcp',
     '--tcp-flags', 'SYN,RST', 'SYN', '-m', 'comment', '--comment', 'MD-Next AWG MSS',
     '-j', 'TCPMSS', '--clamp-mss-to-pmtu')
    for ingress, egress in (('awg0', TUN), (TUN, 'awg0'))
)


def run(*args, check=True, input=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=10)
    if check and result.returncode:
        raise RuntimeError(f'Не удалось выполнить {args[0]} {args[1]}: {result.stderr.strip()[:240]}')
    return result


@contextmanager
def routing_lock(timeout=20):
    if fcntl is None:
        raise RuntimeError('Маршрутизация AWG поддерживается только в Linux')
    if any(p.is_symlink() for p in (STATE.parent, *STATE.parent.parents)):
        raise RuntimeError('Небезопасный каталог состояния маршрута AWG')
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(STATE.parent/'awg-routing.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('Небезопасная блокировка маршрута AWG')
        deadline = time.monotonic()+timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Маршрут AWG уже обновляется; повторите попытку после завершения операции') from None
                time.sleep(.05)
        yield
    finally:
        os.close(fd)


def native_chain():
    """Ошибка iptables -S не доказывает отсутствие цепочки после nft restore."""
    if not shutil.which('nft'):
        raise RuntimeError('Не удалось прочитать цепочку AWG; для диагностики iptables-nft требуется nft')
    result = run('nft', '-j', 'list', 'table', 'ip', 'filter', check=False)
    if result.returncode:
        tables = json.loads(run('nft', '-j', 'list', 'tables').stdout)['nftables']
        if any(row.get('table', {}).get('family') == 'ip' and row['table'].get('name') == 'filter' for row in tables):
            raise RuntimeError('Не удалось прочитать существующую таблицу фильтрации; маршрут AWG не изменён')
        return None
    rows = json.loads(result.stdout)['nftables']
    chain = next((row['chain'] for row in rows if row.get('chain', {}).get('name') == CHAIN), None)
    if chain is None:
        return None
    return chain, [row['rule'] for row in rows if row.get('rule', {}).get('chain') == CHAIN]


def validate_native_chain(data):
    chain, rules = data
    if any(key in chain for key in ('hook', 'type', 'policy', 'dev')):
        raise RuntimeError('Цепочка AWG занята чужой политикой; правила не изменены')
    denied = False
    for rule in rules:
        expr = [e for e in rule['expr'] if not (
            set(e) == {'counter'} and isinstance(e['counter'], dict)
            and set(e['counter']) <= {'packets', 'bytes'}
            and all(type(v) is int and v >= 0 for v in e['counter'].values()))]
        accepted = any(expr == [{'match': {'op': '==', 'left': {'meta': {'key': 'oifname'}}, 'right': interface}}, {'accept': None}]
                       for interface in (TUN, 'awg0'))
        rejected = expr in ([{'reject': {'type': 'icmp', 'expr': 'port-unreachable'}}], [{'drop': None}])
        if rule.get('comment') or not (accepted or rejected) or (accepted and denied):
            raise RuntimeError('В цепочке AWG есть неизвестные правила; автоматическая замена запрещена')
        denied |= rejected


def chain_status():
    result = run('iptables', '-w', '5', '-S', CHAIN, check=False)
    if result.returncode == 0:
        return 'iptables', None
    if shutil.which('nft'):
        data = native_chain()
        return ('native', data) if data is not None else ('missing', None)
    if result.returncode == 1 and 'No chain/target/match' in getattr(result, 'stderr', ''):
        return 'missing', None
    raise RuntimeError('Не удалось проверить цепочку AWG; правила не изменены')


def repair_native_chain(data):
    validate_native_chain(data)
    # Один атомарный batch: цепочка никогда не остаётся пустой. Native REJECT
    # после текстового nft restore не читается iptables-nft; DROP сохраняет
    # запрет обхода TUN и переживает дальнейшие snapshot/restore.
    script = (f'flush chain ip filter {CHAIN}\n'
              f'add rule ip filter {CHAIN} oifname "{TUN}" counter accept\n'
              f'add rule ip filter {CHAIN} oifname "awg0" counter accept\n'
              f'add rule ip filter {CHAIN} counter drop\n')
    run('nft', '-c', '-f', '-', input=script)
    run('nft', '-f', '-', input=script)
    run('iptables', '-w', '5', '-S', CHAIN)


def firewall_rule(*args, table=None):
    prefix = ('iptables', '-w', '5')
    if table:
        prefix += ('-t', table)
    if run(*prefix, '-C', *args, check=False).returncode:
        run(*prefix, '-I', *args)


def remove(kind):
    # Удаляются только точные правила, которыми управляет MD-Next.
    mtu.remove()
    for rule in MSS_RULES:
        if not run('iptables', '-w', '5', '-t', 'mangle', '-C', *rule, check=False).returncode:
            run('iptables', '-w', '5', '-t', 'mangle', '-D', *rule)
    args = ('FORWARD', '-i', 'awg0', '-j', CHAIN)
    if not run('iptables', '-w', '5', '-C', *args, check=False).returncode:
        run('iptables', '-w', '5', '-D', *args)
    args = ('FORWARD', '-i', TUN, '-o', 'awg0', '-j', 'ACCEPT')
    if not run('iptables', '-w', '5', '-C', *args, check=False).returncode:
        run('iptables', '-w', '5', '-D', *args)
    if kind == 'native':
        run('nft', '-f', '-', input=f'flush chain ip filter {CHAIN}\ndelete chain ip filter {CHAIN}\n')
    elif kind == 'iptables':
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


def _remove_owned():
    if STATE.is_symlink():
        raise RuntimeError('Небезопасный маркер маршрута AWG')
    if STATE.exists():
        validate_marker()
        validate_ownership()
        kind, data = chain_status()
        if kind == 'native':
            validate_native_chain(data)
        remove(kind)
        STATE.unlink()


def validate_marker():
    info = STATE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or STATE.read_text() != 'MD-Next AWG routing v1\n':
        raise RuntimeError('Не подтверждена принадлежность маршрута AWG; правила не изменены')


def remove_owned():
    with routing_lock():
        _remove_owned()


def apply(config_path):
    with routing_lock():
        _apply(config_path)


def _apply(config_path):
    if STATE.is_symlink():
        raise RuntimeError('Небезопасный маркер маршрута AWG')
    config = json.loads(Path(config_path).read_text())
    enabled = any(i.get('protocol') == 'tun' and i.get('tag') == TAG
                  and i.get('settings', {}).get('name') == TUN for i in config.get('inbounds', []))
    if not enabled:
        _remove_owned()
        return
    validate_ownership()
    kind, data = chain_status()
    previously_owned = STATE.exists()
    if not previously_owned:
        if kind != 'missing':
            raise RuntimeError('Цепочка MDNEXT_AWG_OUT уже занята; маршрут AWG не изменён')
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text('MD-Next AWG routing v1\n')
        STATE.chmod(0o600)
    else:
        validate_marker()
    if kind == 'native':
        repair_native_chain(data)
    elif kind == 'missing':
        creation = run('iptables', '-w', '5', '-N', CHAIN, check=False)
        if creation.returncode:
            # Независимый nft restore может вернуть цепочку между проверками.
            # Новый маркер при конфликте не даёт присвоить чужую цепочку.
            if not previously_owned:
                STATE.unlink()
                raise RuntimeError('Не удалось создать новую цепочку AWG; чужие правила не изменены')
            current, restored = chain_status()
            if current == 'native':
                repair_native_chain(restored)
            elif current != 'iptables':
                raise RuntimeError('Не удалось создать цепочку AWG: '+getattr(creation,'stderr','').strip()[:240])
    firewall_rule(CHAIN, '-o', TUN, '-j', 'ACCEPT')
    firewall_rule(CHAIN, '-o', 'awg0', '-j', 'ACCEPT')
    if run('iptables', '-w', '5', '-C', CHAIN, '-j', 'DROP', check=False).returncode:
        run('iptables', '-w', '5', '-A', CHAIN, '-j', 'DROP')
    # Сначала добавлен DROP: удаление прежнего REJECT не открывает выход.
    while not run('iptables', '-w', '5', '-C', CHAIN, '-j', 'REJECT', check=False).returncode:
        run('iptables', '-w', '5', '-D', CHAIN, '-j', 'REJECT')
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
    mtu.apply()
    # Снимаем правила прежнего выпуска после успешного применения новых.
    for rule in MSS_RULES:
        if not run('iptables', '-w', '5', '-t', 'mangle', '-C', *rule, check=False).returncode:
            run('iptables', '-w', '5', '-t', 'mangle', '-D', *rule)
    run('sysctl', '-w', 'net.ipv4.ip_forward=1')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/usr/local/etc/xray/config.json')
    parser.add_argument('--remove', action='store_true', help='Remove only routing owned by MD-Next')
    args = parser.parse_args()
    try:
        if args.remove:
            remove_owned()
        else:
            apply(args.config)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
    print('Маршрут AmneziaWG согласован с Xray')
