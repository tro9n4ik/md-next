"""Изолированная проверка UDP hopping/nftables/TUN. По умолчанию только план."""
import argparse
import os
from pathlib import Path
import re
import secrets
import select
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    if not args.run:
        print('План: новый network namespace -> loopback -> отдельная NAT-таблица -> 32 AEAD-пробы через меняющиеся порты -> TUN -> удаление namespace. Сеть хоста не меняется.')
        return
    if os.geteuid()!=0:raise SystemExit('Для изолированного namespace нужен root')
    root=Path(__file__).resolve().parents[1]
    binary=root/'mdnt'
    if not binary.is_file():raise SystemExit('Сначала соберите go build -o mdnt ./cmd/mdnt')
    name='mdnt-'+secrets.token_hex(4)
    created=False;server=None
    def run(command,**kw):
        result=subprocess.run(command,capture_output=True,text=True,timeout=120,**kw)
        if result.returncode:raise RuntimeError('Изолированная проверка завершилась ошибкой: '+result.stderr[:1000])
        return result
    try:
        run(['ip','netns','add',name]);created=True
        run(['ip','-n',name,'link','set','lo','up'])
        nft='table ip mdnt_test {\n chain output {\n  type nat hook output priority dstnat; policy accept;\n  ip daddr 127.0.0.1 udp sport != 38000 udp dport 38010-38013 redirect to 38000;\n }\n}\n'
        run(['ip','netns','exec',name,'nft','-c','-f','-'],input=nft)
        run(['ip','netns','exec',name,'nft','-f','-'],input=nft)
        with tempfile.TemporaryDirectory(prefix='mdnt-session-') as folder:
            key=Path(folder)/'key';key.write_text(secrets.token_hex(32));key.chmod(0o600)
            base=['ip','netns','exec',name,str(binary),'-key-file',str(key),'-interval','1s']
            server=subprocess.Popen(base+['-mode','serve','-state-dir',folder+'/server'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            if not select.select([server.stdout],[],[],5)[0]:raise RuntimeError('Сервер не запустился')
            if 'ready' not in server.stdout.readline():raise RuntimeError('Ошибка старта сервера')
            # An unauthenticated datagram must not elicit any response.
            run(['ip','netns','exec',name,'python3','-c',
                 "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(0.3); s.sendto(bytes(80),('127.0.0.1',38010));\ntry: s.recv(1200); raise RuntimeError('Unauthenticated response')\nexcept socket.timeout: pass"])
            print('UNAUTHENTICATED_NO_RESPONSE_OK')
            result=run(base+['-mode','probe','-state-dir',folder+'/client','-base','38010','-ports','4','-count','32'])
            ports=set(re.findall(r'port (\d+)',result.stdout))
            if result.stdout.count(' OK')!=32 or len(ports)<2:raise RuntimeError('Проверка ротации портов не прошла')
            print('AEAD_UDP_NFT_OK packets=32 distinct_destination_ports='+str(len(ports)))
        result=run(['ip','netns','exec',name,'env','MDNEXT_ISOLATED_NETNS=1','GOMAXPROCS=2','GOMEMLIMIT=192MiB','go','test','-p','1','-run','TestIsolatedTUN','-count=1','.'],cwd=root)
        print('TUN_NAMESPACE_OK',result.stdout.strip())
    finally:
        if server is not None:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
        if created:
            run(['ip','netns','del',name])
            if name in run(['ip','netns','list']).stdout:raise RuntimeError('Namespace не удалён')
            print('NAMESPACE_AND_FIREWALL_REMOVED')


if __name__=='__main__':main()
