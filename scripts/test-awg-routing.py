"""Root-only: повторное применение AWG после nft restore, только в namespace."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=30, **kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if not args.run:
        print('План: один namespace, snapshot/restore, параллельные apply/remove, проверка чужих правил и очистка.');return
    name='mdart'+uuid.uuid4().hex[:8]
    helper=Path(__file__).with_name('awg-routing.py')
    created=False
    def execute(*command,check=True,input=None):
        result=run('ip','netns','exec',name,*command,input=input)
        if check and result.returncode:raise RuntimeError(result.stderr[-1000:])
        return result
    driver="""import runpy,sys
from pathlib import Path
m=runpy.run_path(sys.argv[1]);state=Path(sys.argv[2]);m['apply'].__globals__['STATE']=state
assert m['apply'].__globals__['STATE']==state
m['remove_owned']() if sys.argv[3]=='remove' else m['apply'](sys.argv[3])
"""
    try:
        result=run('ip','netns','add',name)
        if result.returncode:raise RuntimeError(result.stderr)
        created=True
        for interface in ('awg0','mdawg'):
            execute('ip','link','add',interface,'type','dummy');execute('ip','link','set',interface,'up')
        execute('ip','address','add','10.254.44.1/24','dev','awg0')
        execute('iptables','-N','MDNEXT_AWG_OUT')
        for interface in ('awg0','mdawg'):execute('iptables','-A','MDNEXT_AWG_OUT','-o',interface,'-j','ACCEPT')
        execute('iptables','-A','MDNEXT_AWG_OUT','-j','REJECT')
        execute('iptables','-A','FORWARD','-i','awg0','-j','MDNEXT_AWG_OUT')
        execute('iptables','-A','FORWARD','-i','external0','-j','DROP')
        execute('nft','-f','-',input='table inet foreign_probe {\n chain keep {\n  counter drop\n }\n}\n')
        snapshot=execute('nft','list','ruleset').stdout
        execute('nft','-f','-',input='flush ruleset\n'+snapshot)
        old_probe=execute('iptables','-S','MDNEXT_AWG_OUT',check=False)
        if old_probe.returncode:
            assert execute('iptables','-N','MDNEXT_AWG_OUT',check=False).returncode!=0
        foreign=execute('nft','-j','list','table','inet','foreign_probe').stdout
        with tempfile.TemporaryDirectory(prefix='mdnext-routing-test-') as folder:
            root=Path(folder);state=root/'owned';config=root/'xray.json'
            state.write_text('MD-Next AWG routing v1\n');state.chmod(0o600)
            config.write_text(json.dumps({'inbounds':[{'protocol':'tun','tag':'awg-in','settings':{'name':'mdawg'}}]}))
            command=[sys.executable,'-c',driver,str(helper),str(state),str(config)]
            execute(*command)
            # Ещё один roundtrip обязан сохранять читаемость iptables.
            snapshot=execute('nft','list','ruleset').stdout
            execute('nft','-f','-',input='flush ruleset\n'+snapshot)
            assert execute('iptables','-S','MDNEXT_AWG_OUT').returncode==0
            foreign=execute('nft','-j','list','table','inet','foreign_probe').stdout
            processes=[subprocess.Popen(['ip','netns','exec',name,*command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(6)]
            try:
                for process in processes:
                    _,error=process.communicate(timeout=40);assert process.returncode==0,error[-1000:]
            finally:
                for process in processes:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
            chain=execute('iptables','-S','MDNEXT_AWG_OUT').stdout
            assert len([l for l in chain.splitlines() if l.startswith('-A ')])==3,chain
            assert '-j DROP' in chain and '-j REJECT' not in chain
            forward=execute('iptables','-S','FORWARD').stdout
            assert forward.count('-i awg0 -j MDNEXT_AWG_OUT')==1 and '-i external0 -j DROP' in forward
            assert execute('nft','-j','list','table','inet','foreign_probe').stdout==foreign
            rules=json.loads(execute('ip','-j','rule','show').stdout)
            assert sum(r.get('priority')==10086 for r in rules)==1
            # Apply и remove используют ту же блокировку и могут пересекаться.
            cleanup=[sys.executable,'-c',driver,str(helper),str(state),'remove']
            processes=[subprocess.Popen(['ip','netns','exec',name,*c],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for c in (cleanup,command)]
            try:
                for process in processes:
                    _,error=process.communicate(timeout=40);assert process.returncode==0,error[-1000:]
            finally:
                for process in processes:
                    if process.poll() is None:process.kill();process.wait(timeout=5)
            execute(*command);execute(*cleanup)
            assert not state.exists()
            assert execute('iptables','-S','MDNEXT_AWG_OUT',check=False).returncode!=0
            assert '-i external0 -j DROP' in execute('iptables','-S','FORWARD').stdout
            assert execute('nft','-j','list','table','inet','foreign_probe').stdout==foreign
        print(json.dumps({'legacy_failure_reproduced':bool(old_probe.returncode),'nft_roundtrip':'ok','parallel_apply':6,'parallel_remove_apply':'ok','foreign_rules_preserved':True}))
    finally:
        if created:run('ip','netns','del',name)


if __name__=='__main__':main()
