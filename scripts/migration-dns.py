"""Проверка DNS чистого целевого VPS; исправление только по явному вызову prepare."""
import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import tempfile

CONFIG = Path('/etc/systemd/resolved.conf.d/70-md-next-verified-dns.conf')
HOSTS = ('github.com', 'api.github.com', 'pypi.org', 'registry.npmjs.org')
PROVIDERS = ('8.8.8.8#dns.google', '1.1.1.1#cloudflare-dns.com')


def run(args, timeout=8):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True,
                          env={**os.environ, 'LC_ALL': 'C'})


def exact(stream, size):
    result = b''
    while len(result) < size:
        piece = stream.recv(size-len(result))
        if not piece:
            raise ValueError('Неполный ответ DNS')
        result += piece
    return result


def probe(server, host):
    address, _, name = server.partition('#')
    ip = ipaddress.ip_address(address)
    query = secrets.token_bytes(2) + struct.pack('!HHHHH', 0x100, 1, 0, 0, 0)
    query += b''.join(bytes([len(label)]) + label.encode('ascii') for label in host.split('.'))
    query += b'\0' + struct.pack('!HH', 1, 1)
    if name:
        with socket.create_connection((str(ip), 853), timeout=2) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=name) as stream:
                stream.sendall(struct.pack('!H', len(query))+query)
                size = struct.unpack('!H', exact(stream, 2))[0]
                reply = exact(stream, size)
    else:
        with socket.socket(socket.AF_INET6 if ip.version == 6 else socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.settimeout(2)
            stream.connect((str(ip), 53))
            stream.send(query)
            reply = stream.recv(65535)
    if len(reply) < 12:
        raise ValueError('Короткий ответ DNS')
    identifier, flags, questions, answers, _, _ = struct.unpack('!HHHHHH', reply[:12])
    if reply[:2] != query[:2] or not flags & 0x8000 or flags & 0x020f or questions != 1 or not answers:
        raise ValueError('DNS отказал или не вернул адрес')


def bounded_probe(server, host):
    run([sys.executable, __file__, "--probe", server, host], timeout=6)


def system_check(hosts):
    # A child bounds getaddrinfo too: socket timeout alone does not bound DNS.
    for host in hosts:
        run([sys.executable, '-c',
             'import socket,sys;socket.getaddrinfo(sys.argv[1],443,type=socket.SOCK_STREAM)', host])


def static_servers():
    result = []
    for line in Path('/etc/resolv.conf').read_text().splitlines():
        fields = line.split('#',1)[0].split()
        if len(fields) >= 2 and fields[0] == 'nameserver':
            result.append(str(ipaddress.ip_address(fields[1])))
    return result


def configured_servers():
    result = run(['resolvectl', 'dns'])
    domains = run(['resolvectl','domain']).stdout
    global_route = any(line.startswith('Global:') and '~.' in line.split() for line in domains.splitlines())
    servers = set()
    for line in result.stdout.splitlines():
        if global_route and not line.startswith('Global:'):
            continue
        for token in line.split(': ', 1)[-1].split():
            try:
                ipaddress.ip_address(token.partition('#')[0])
                servers.add(token)
            except ValueError:
                continue
    return sorted(servers)


def custom_dns_domains():
    result = run(['resolvectl','domain'])
    return any(domain not in {'~.','.'} for line in result.stdout.splitlines()
               for domain in line.split(': ',1)[-1].split() if ': ' in line)


def atomic_config(content, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix='.migration-dns-', dir=CONFIG.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(content); output.flush(); os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, CONFIG)
        if os.name == 'posix':
            directory = os.open(CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def ensure_dns(hosts=HOSTS, *, repair=False, notify=lambda stage: None):
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'systemd-resolved'],
                            capture_output=True, timeout=5).returncode == 0
    try:
        system_check(hosts)
        for server in (configured_servers() if active else static_servers()):
            for host in HOSTS[:2]:
                bounded_probe(server, host)
        return False
    except (OSError, ValueError, subprocess.SubprocessError):
        if not repair or not active:
            raise RuntimeError('Сначала настройте DNS целевого VPS') from None
    # Split/private DNS needs an operator; never replace its routing domains.
    if custom_dns_domains():
        raise RuntimeError('Нестандартные домены DNS требуют ручной подготовки')
    # Do not create a dependency on AdGuard startup.
    providers = []
    for server in PROVIDERS:
        try:
            for host in HOSTS:
                bounded_probe(server, host)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        providers.append(server)
    if not providers:
        raise RuntimeError('Нет доступного проверенного DNS-over-TLS')
    notify('TARGET_DNS_REPAIR')
    if CONFIG.is_symlink() or CONFIG.parent.is_symlink():
        raise ValueError('Ссылка в конфигурации DNS')
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    previous = CONFIG.read_bytes() if CONFIG.exists() else None
    content = ('[Resolve]\nDNS=\nDNS=' + ' '.join(providers) +
               '\nFallbackDNS=\nDomains=~.\nDNSOverTLS=yes\n').encode()
    previous_mode = CONFIG.stat().st_mode & 0o777 if CONFIG.exists() else 0o644
    atomic_config(content)
    try:
        run(['systemctl', 'restart', 'systemd-resolved'], timeout=15)
        for _ in range(2):
            run(['resolvectl', 'flush-caches'])
            system_check(hosts)
    except Exception:
        if previous is None:
            CONFIG.unlink(missing_ok=True)
        else:
            atomic_config(previous, previous_mode)
        run(['systemctl', 'restart', 'systemd-resolved'], timeout=15)
        raise
    return True


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--probe':
        probe(sys.argv[2], sys.argv[3])
    elif sys.argv[1:] == ['--check']:
        ensure_dns(repair=False)
    else:
        raise SystemExit('Только --check или внутренний --probe')
