#!/usr/bin/env python3
"""Проверка реального AWG-клиента без изменения профилей и основного маршрута."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid


def run(*args, check=True, timeout=30, input=None):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # stderr awg может содержать ключ из ошибочной конфигурации.
        raise RuntimeError(f'Не выполнена команда {args[0]} (код {result.returncode})')
    return result


def stop(*args):
    raise RuntimeError('Проверка прервана')


def check(args):
    if os.geteuid() != 0:
        raise RuntimeError('Запустите проверку от root')
    token = uuid.uuid4().hex[:8]
    namespace, interface = 'mdcheck'+token, 'mdc'+token
    public = None
    peer_added = namespace_added = interface_added = False
    original = Path(args.config).read_text().split('[Peer]')[0]
    # Только параметры протокола; hooks, DNS, ключ сервера и routes не копируются.
    names = {'Jc','Jmin','Jmax','S1','S2','S3','S4','H1','H2','H3','H4',
             'HeaderProtectionKey','ContentPaddingAddition','RandomTrailers','DisableCookies',
             'I1','I2','I3','I4','I5'}
    parameters = [line for line in original.splitlines() if '=' in line and line.split('=',1)[0].strip() in names]
    addresses = json.loads(run('ip','-j','-4','addr','show',args.interface).stdout)
    addr = next(a for device in addresses for a in device.get('addr_info',[]) if a['scope']=='global')
    network = ipaddress.ip_network(f"{addr['local']}/{addr['prefixlen']}",strict=False)
    occupied = [ipaddress.ip_network(cidr.rstrip(',')) for line in run('awg','show',args.interface,'allowed-ips').stdout.splitlines()
                for cidr in line.split()[1:] if cidr != '(none)' and ':' not in cidr]
    address = next((str(host) for index in range(network.num_addresses-2,0,-1) for host in (network[index],)
                    if str(host)!=addr['local'] and not any(host in used for used in occupied)),None)
    if not address:
        raise RuntimeError('Нет свободного адреса для временного клиента')
    private = run('awg','genkey').stdout.strip()
    public = run('awg','pubkey',input=private+'\n').stdout.strip()
    server_public = run('awg','show',args.interface,'public-key').stdout.strip()
    port = run('awg','show',args.interface,'listen-port').stdout.strip()
    endpoint = args.endpoint or f'127.0.0.1:{port}'
    config = '[Interface]\nPrivateKey = '+private+'\n'+'\n'.join(parameters)+'\n\n[Peer]\nPublicKey = '+server_public+'\nEndpoint = '+endpoint+'\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n'
    prefix = ('ip','netns','exec',namespace)
    start = time.monotonic()
    handshakes = []
    try:
        run('ip','netns','add',namespace); namespace_added = True
        # UDP-сокет остаётся в исходном namespace; основной маршрут не меняется.
        run('ip','link','add',interface,'type','amneziawg'); interface_added = True
        run('ip','link','set',interface,'netns',namespace)
        run(*prefix,'ip','link','set','lo','up')
        with tempfile.NamedTemporaryFile(mode='w',dir='/run',prefix='md-awg-check-') as f:
            f.write(config); f.flush()
            run(*prefix,'awg','setconf',interface,f.name)
        run(*prefix,'ip','addr','add',address+'/32','dev',interface)
        # Ключ должен быть зарегистрирован до up: up сразу запускает keepalive.
        run('awg','set',args.interface,'peer',public,'allowed-ips',address+'/32'); peer_added = True
        run(*prefix,'ip','link','set',interface,'up','mtu',str(args.mtu))
        run(*prefix,'ip','route','add','default','dev',interface)
        cycle = 0
        while cycle < args.rounds or time.monotonic()-start < args.duration:
            cycle += 1
            trace = run(*prefix,'curl','-4','--max-time','20','-sS','--resolve',
                        'www.cloudflare.com:443:104.16.123.96','https://www.cloudflare.com/cdn-cgi/trace').stdout
            values = dict(line.split('=',1) for line in trace.splitlines() if '=' in line)
            if args.expected_ip and values.get('ip')!=args.expected_ip:
                raise RuntimeError('Выходной IP не соответствует ожидаемому')
            if args.expected_country and values.get('loc')!=args.expected_country.upper():
                raise RuntimeError('Страна выхода не соответствует ожидаемой')
            dns = "import socket,struct;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(10);q=struct.pack('!6H',4321,256,1,0,0,0)+b'\\x07example\\x03com\\0'+struct.pack('!2H',1,1);s.sendto(q,('1.1.1.1',53));r=s.recv(4096);assert r[:2]==q[:2] and r[3]&15==0"
            run(*prefix,'python3','-c',dns)
            if args.download_url:
                size = run(*prefix,'curl','-4','--max-time','30','--fail','-sS',args.download_url,
                           '-o','/dev/null','-w','%{size_download}',timeout=35).stdout
                if int(size)<args.minimum_bytes:
                    raise RuntimeError('Загрузка завершилась с недостаточным количеством данных')
            handshake = int(run(*prefix,'awg','show',interface,'latest-handshakes').stdout.split()[1])
            if not handshake:
                raise RuntimeError('Рукопожатие не завершено')
            handshakes.append(handshake)
            print(f"Проверка {cycle}: HTTPS и UDP DNS работают; выход {values.get('ip')} ({values.get('loc')})",flush=True)
            if cycle < args.rounds or time.monotonic()-start < args.duration:
                time.sleep(10)
        if args.duration >= 180 and handshakes[-1] <= handshakes[0]:
            raise RuntimeError('За время проверки не произошло обновление ключей')
        print('Проверка завершена; обновление ключей: '+('подтверждено' if handshakes[-1]>handshakes[0] else 'проверка была короткой'),flush=True)
    finally:
        if peer_added: run('awg','set',args.interface,'peer',public,'remove',check=False)
        if namespace_added: run('ip','netns','del',namespace,check=False)
        if interface_added: run('ip','link','del',interface,check=False)
        print('Временный клиент удалён',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface',default='awg0')
    parser.add_argument('--config',default='/etc/amnezia/amneziawg/awg0.conf')
    parser.add_argument('--endpoint',help='Внешний UDP-ретранслятор IP:порт, необязательно')
    parser.add_argument('--expected-ip')
    parser.add_argument('--expected-country')
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--duration',type=int,default=0,help='Минимальная длительность в секундах; 180 для проверки обновления ключей')
    parser.add_argument('--mtu',type=int,default=1420)
    parser.add_argument('--download-url')
    parser.add_argument('--minimum-bytes',type=int,default=1048576)
    args = parser.parse_args()
    if args.rounds<1 or args.duration<0 or not 576<=args.mtu<=1420:
        parser.error('Некорректные число проверок, длительность или MTU')
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:check(args)
    except Exception as error:
        print('Ошибка: '+str(error),flush=True)
        raise SystemExit(1)
