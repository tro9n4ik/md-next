"""Независимый таймер возврата firewall перед удалённым изменением сети."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

STATE = Path("/var/lib/md-next/network-latch")
LEASE_SECONDS = 180
MAX_SECONDS = 3600
TICK_SECONDS = 15


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, check=True, timeout=20)


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def folder(token):
    if not re.fullmatch("[a-f0-9]{32}", token):
        raise ValueError("Неверный идентификатор страховки")
    path = STATE / token
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise ValueError("Небезопасное состояние страховки")
    return path


def unit(token):
    return "mdnext-net-rollback-" + token


def clear_active(token):
    path=STATE/'active'
    if path.exists() and path.read_text()==token:path.unlink()


def replace(path, data):
    temporary = path.with_name('.state-' + secrets.token_hex(16))
    try:
        write(temporary, data)
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def lease(path):
    data = json.loads((path / 'lease.json').read_text())
    if data['boot'] != boot_id():
        raise RuntimeError('Страховка относится к другой загрузке сервера')
    return data


def live(path):
    data = lease(path)
    return time.monotonic() < min(data['deadline'], data['hard_deadline'])


def compat_snapshots(path):
    # nft text loses xt target metadata (notably REJECT). Restore only tables
    # demonstrably fully represented by iptables-save after the native snapshot.
    rows = json.loads(run('nft', '-j', 'list', 'ruleset').stdout)['nftables']
    tables = {(r['rule']['family'], r['rule']['table']) for r in rows
              if 'rule' in r and '"xt"' in json.dumps(r['rule'].get('expr', []))}
    for family in ('ip', 'ip6'):
        snapshots = []
        for _, table in sorted(t for t in tables if t[0] == family):
            if not re.fullmatch('[A-Za-z0-9_]+', table):
                raise RuntimeError('Неизвестная таблица совместимости firewall')
            name = 'iptables' if family == 'ip' else 'ip6tables'
            saved = run(name + '-save', '-c', '-t', table)
            chains = {r['chain']['name'] for r in rows if 'chain' in r
                      and (r['chain']['family'], r['chain']['table']) == (family, table)}
            exported = {line.split()[0][1:] for line in saved.stdout.splitlines() if line.startswith(':')}
            builtins={'filter':{'INPUT','FORWARD','OUTPUT'},'security':{'INPUT','FORWARD','OUTPUT'},
                      'nat':{'PREROUTING','INPUT','OUTPUT','POSTROUTING'},
                      'mangle':{'PREROUTING','INPUT','FORWARD','OUTPUT','POSTROUTING'},'raw':{'PREROUTING','OUTPUT'}}.get(table,set())
            for row in rows:
                chain=row.get('chain',{})
                if (chain.get('family'),chain.get('table'))!=(family,table):continue
                if 'hook' in chain:
                    priority={'filter':0,'security':50,'mangle':-150,'raw':-300}.get(table)
                    if table=='nat':priority=100 if chain.get('hook') in {'input','postrouting'} else -100
                    if chain.get('name') not in builtins or chain.get('hook')!=chain['name'].lower() or chain.get('prio')!=priority or chain.get('type')!=('nat' if table=='nat' else 'filter') or chain.get('policy') not in {'accept','drop'} or chain.get('dev') or chain.get('flags'):
                        raise RuntimeError('Нестандартный hook firewall нельзя безопасно сохранить')
            # iptables-save synthesizes absent empty ACCEPT builtins.
            extra=exported-chains
            implicit_ok=extra<=builtins and all(line.split()[1]=='ACCEPT' for line in saved.stdout.splitlines()
                                               if line.startswith(':') and line.split()[0][1:] in extra)
            rules = sum(1 for r in rows if 'rule' in r
                        and (r['rule']['family'], r['rule']['table']) == (family, table))
            if saved.stderr.strip() or not chains<=exported or not implicit_ok or len(re.findall(r'(?m)^(?:\[\d+:\d+\] )?-A ',saved.stdout)) != rules:
                raise RuntimeError('Смешанную таблицу firewall нельзя безопасно сохранить')
            run(name + '-restore', '--test', '-c', input=saved.stdout)
            snapshots.append(saved.stdout)
        if snapshots:
            write(path / ('compat-' + family + '.rules'), '\n'.join(snapshots))


def arm():
    if (STATE / "active").exists():
        raise RuntimeError("Предыдущая страховка ещё действует")
    token = secrets.token_hex(16)
    path = STATE / token
    path.mkdir(mode=0o700)
    try:
        if shutil.which("nft"):
            source = "flush ruleset\n" + run("nft", "list", "ruleset").stdout
            run("nft", "-c", "-f", "-", input=source)
            write(path / "nft.rules", source)
            compat_snapshots(path)
        else:
            for name in ("iptables", "ip6tables"):
                write(path / (name + ".rules"), run(name + "-save").stdout)
        # Таймер использует собственную копию и переживает обрыв SSH.
        write(path / "restore.py", Path(__file__).read_text())
        now = time.monotonic()
        write(path / 'lease.json', json.dumps({'boot': boot_id(), 'deadline': now + LEASE_SECONDS,
                                              'hard_deadline': now + MAX_SECONDS}))
        write(path / "status", "armed")
        write(STATE / "active", token)
        run("systemd-run", "--quiet", "--unit=" + unit(token), "--on-active="+str(TICK_SECONDS)+"s", "--on-unit-active="+str(TICK_SECONDS)+"s",
            "--timer-property=AccuracySec=1s", "--property=Type=oneshot",
            "/usr/bin/python3", str(path / "restore.py"), "tick", token)
    except Exception:
        (STATE / "active").unlink(missing_ok=True)
        shutil.rmtree(path)
        raise
    return token


def renew(token):
    path = folder(token)
    if (path / 'status').read_text() != 'armed' or not live(path):
        raise RuntimeError('Срок страховки истёк; продолжение запрещено')
    data = lease(path)
    data['deadline'] = min(time.monotonic() + LEASE_SECONDS, data['hard_deadline'])
    replace(path / 'lease.json', json.dumps(data))


def tick(token):
    path = folder(token)
    if (path / 'status').read_text() == 'armed' and live(path):
        return
    rollback(token)


def rollback(token):
    path = folder(token)
    if (path / "status").read_text() not in ("armed", "expired", "restore_failed"):
        return
    replace(path / 'status', 'expired')
    if (path / "nft.rules").is_file():
        run("nft", "-f", str(path / "nft.rules"))
        for family, name in (('ip', 'iptables'), ('ip6', 'ip6tables')):
            snapshot = path / ('compat-' + family + '.rules')
            if snapshot.is_file(): run(name + '-restore', '-c', input=snapshot.read_text())
    else:
        for name in ("iptables", "ip6tables"):
            run(name + "-restore", input=(path / (name + ".rules")).read_text())
    replace(path / "status", "restored")
    clear_active(token)


def confirm(token):
    path = folder(token)
    if (path / "status").read_text() not in ("armed", "confirmed"):
        raise RuntimeError("Срок страховки истёк или правила уже восстановлены")
    if (path/'status').read_text()=='armed' and not live(path):
        raise RuntimeError('Срок страховки истёк; подтверждение запрещено')
    # Статус меняется под тем же lock, который использует таймер.
    replace(path / "status", "confirmed")
    clear_active(token)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("arm", "confirm", "rollback", "renew", "tick", "status"))
    parser.add_argument("token", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Требуется root")
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o077:
        raise RuntimeError("Небезопасный каталог страховки")
    with (STATE / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "arm":
            print(json.dumps({"token": arm()}))
        elif args.action == "rollback":
            rollback(args.token)
        elif args.action == 'renew':
            renew(args.token)
        elif args.action == 'tick':
            tick(args.token)
        elif args.action == 'status':
            path = folder(args.token)
            print(json.dumps({'status': (path / 'status').read_text(),
                              'live': live(path) if (path / 'lease.json').exists() else False}))
        else:
            confirm(args.token)
    if args.action == "confirm" or args.action == "tick" and (folder(args.token) / "status").read_text() == "restored":
        # Остановку службы выполняем после снятия lock.
        run("systemctl", "stop", unit(args.token) + ".timer")
        # После снятия transient-таймера systemd может уже выгрузить ещё не
        # запускавшуюся службу. Код 5 означает отсутствие такой службы.
        if args.action=='confirm':
            result = subprocess.run(["systemctl", "stop", unit(args.token) + ".service"],
                                    capture_output=True, text=True, timeout=20)
            if result.returncode not in (0, 5):
                raise RuntimeError("Не удалось остановить службу страховки")
        # Keep a small root-only confirmation for activation reconciliation.
        path = folder(args.token)
        for name in ('nft.rules', 'iptables.rules', 'ip6tables.rules', 'compat-ip.rules', 'compat-ip6.rules', 'controller-probe.py'):
            (path / name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
