"""Изолированный systemd/namespace тест продления, предела аренды и xt restore."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


def run(*args,input=None):
    result=subprocess.run(args,input=input,capture_output=True,text=True,timeout=25)
    if result.returncode:raise RuntimeError(result.stderr[-1500:])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true')
    if not parser.parse_args().run:
        print('План: отдельный namespace, аренда 4/18 секунд, настоящий systemd, xt REJECT и очистка.');return
    namespace='mdl'+uuid.uuid4().hex[:9];units=[]
    run('ip','netns','add',namespace)
    try:
        with tempfile.TemporaryDirectory(prefix='mdnext-lease-') as directory:
            root=Path(directory);state=root/'state';state.mkdir(mode=0o700)
            source=root/'latch.py'
            text=Path(__file__).with_name('network-latch.py').read_text()
            text=text.replace('Path("/var/lib/md-next/network-latch")','Path('+repr(str(state))+')')
            text=text.replace('LEASE_SECONDS = 180','LEASE_SECONDS = 4').replace('MAX_SECONDS = 3600','MAX_SECONDS = 18').replace('TICK_SECONDS = 15','TICK_SECONDS = 1')
            source.write_text(text)
            spec=importlib.util.spec_from_file_location('isolated_lease',source)
            latch=importlib.util.module_from_spec(spec);spec.loader.exec_module(latch)
            def command(*args,input=None):
                if args[0]=='systemd-run':
                    index=args.index('/usr/bin/python3')
                    return run(*args[:index],'/usr/sbin/ip','netns','exec',namespace,*args[index:],input=input)
                # systemctl addresses the host service manager, not namespace networking.
                if args[0]=='systemctl':return run(*args,input=input)
                result=run('ip','netns','exec',namespace,*args,input=input)
                if args[0]=='iptables-save' and result.stderr.strip():
                    print('isolated iptables-save warning:',result.stderr.strip(),flush=True)
                return result
            latch.run=command
            command('iptables','-N','MDNEXT_TEST')
            command('iptables','-A','MDNEXT_TEST','-c','7','700','-j','REJECT','--reject-with','icmp-port-unreachable')
            command('nft','-f','-',input='table inet foreign_probe {\n chain untouched {\n counter accept\n }\n}\n')
            token=latch.arm();units.append(latch.unit(token))
            assert (state/token/'compat-ip.rules').is_file(),'xt metadata must be saved'
            command('nft','-f','-',input='table inet temporary_probe {\n chain extra {\n counter drop\n }\n}\n')
            started=time.monotonic()
            while time.monotonic()-started<10:
                time.sleep(2);latch.renew(token)
            assert (state/token/'status').read_text()=='armed','lease must survive its initial timeout'
            deadline=time.monotonic()+14
            while (state/token/'status').read_text()!='restored' and time.monotonic()<deadline:time.sleep(.2)
            assert (state/token/'status').read_text()=='restored'
            assert '-j REJECT' in command('iptables','-S','MDNEXT_TEST').stdout
            assert '[7:700] -A MDNEXT_TEST' in command('iptables-save','-c','-t','filter').stdout
            rules=command('nft','list','ruleset').stdout
            assert 'foreign_probe' in rules and 'temporary_probe' not in rules
            # Confirmation must survive another CLI invocation and stop its timer.
            second=latch.arm();units.append(latch.unit(second))
            command('/usr/bin/python3',str(source),'confirm',second)
            assert (state/second/'status').read_text()=='confirmed'
            assert not (state/'active').exists()
            assert not (state/second/'nft.rules').exists()
            third=latch.arm(session='a'*32);units.append(latch.unit(third))
            assert latch.lease(state/third)['session']=='a'*32
            command('/usr/bin/python3',str(source),'rollback',third)
            assert (state/third/'status').read_text()=='restored' and not (state/'active').exists()
            print(json.dumps({'renewal_beyond_initial_lease':'ok','independent_expiry':'ok',
                              'xt_reject_roundtrip':'ok','foreign_rules_preserved':True,'confirmed_receipt':'ok','session_cancel_cleanup':'ok'}))
    finally:
        for unit in units:
            subprocess.run(['systemctl','stop',unit+'.timer',unit+'.service'],capture_output=True,timeout=25)
            subprocess.run(['systemctl','reset-failed',unit+'.service'],capture_output=True,timeout=25)
        run('ip','netns','del',namespace)


if __name__=='__main__':main()
