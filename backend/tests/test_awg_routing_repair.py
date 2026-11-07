"""Восстановленная nft-цепочка не должна ошибочно создаваться заново."""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

spec=importlib.util.spec_from_file_location('awg_repair',Path(__file__).resolve().parents[2]/'scripts/awg-routing.py')
routes=importlib.util.module_from_spec(spec);spec.loader.exec_module(routes)


def native(rules=None):
    return ({'family':'ip','table':'filter','name':routes.CHAIN},rules if rules is not None else [
        {'expr':[{'match':{'op':'==','left':{'meta':{'key':'oifname'}},'right':interface}},{'counter':{'packets':12,'bytes':1440}},{'accept':None}]}
        for interface in ['awg0','mdawg']]+[{'expr':[{'counter':{'packets':0,'bytes':0}},{'reject':{'type':'icmp','expr':'port-unreachable'}}]}])


def setup(tmp_path,monkeypatch,owned=True,data=None):
    config=tmp_path/'xray.json';config.write_text(json.dumps({'inbounds':[{'protocol':'tun','tag':'awg-in','settings':{'name':'mdawg'}}]}))
    marker=tmp_path/'owned'
    if owned:marker.write_text('MD-Next AWG routing v1\n')
    monkeypatch.setattr(routes,'STATE',marker);monkeypatch.setattr(routes,'routing_lock',nullcontext)
    if sys.platform=='win32':
        # POSIX-владельцы и mode проверяются в Linux, не эмулируются chmod Windows.
        monkeypatch.setattr(routes,'validate_marker',lambda:None)
    monkeypatch.setattr(routes,'validate_ownership',lambda:None);monkeypatch.setattr(routes.mtu,'apply',lambda:None)
    monkeypatch.setattr(routes,'chain_status',lambda:('native',data or native()))
    calls=[]
    def run(*args,check=True,input=None):
        calls.append((args,input))
        if args[:4]==('ip','-j','rule','show'):return SimpleNamespace(returncode=0,stdout='[]')
        if args[:4]==('ip','-j','-4','address'):return SimpleNamespace(returncode=0,stdout='[]')
        return SimpleNamespace(returncode=1 if '-C' in args and ('REJECT' in args or 'mangle' in args) else 0,stdout='')
    monkeypatch.setattr(routes,'run',run)
    return config,marker,calls


def test_owned_native_chain_is_atomically_repaired_without_creation(tmp_path,monkeypatch):
    config,_,calls=setup(tmp_path,monkeypatch)
    routes.apply(config)
    assert not any('-N' in args for args,_ in calls)
    batches=[text for args,text in calls if args==('nft','-f','-')]
    assert len(batches)==1
    assert 'flush chain ip filter MDNEXT_AWG_OUT' in batches[0]
    assert batches[0].count('counter accept')==2 and 'counter drop' in batches[0]
    assert 'flush ruleset' not in batches[0] and 'delete table' not in batches[0]
    assert calls.index((('nft','-c','-f','-'),batches[0])) < calls.index((('nft','-f','-'),batches[0]))


def test_unowned_native_chain_is_not_adopted(tmp_path,monkeypatch):
    config,marker,calls=setup(tmp_path,monkeypatch,owned=False)
    with pytest.raises(RuntimeError,match='занята'):routes.apply(config)
    assert not marker.exists() and not calls


@pytest.mark.parametrize('rule',[
    {'expr':[{'accept':None}]},
    {'expr':[{'match':{'op':'==','left':{'meta':{'key':'oifname'}},'right':'ens3'}},{'accept':None}]},
    {'comment':'foreign','expr':[{'drop':None}]},
    {'expr':[{'counter':'foreign_accounting'},{'drop':None}]},
])
def test_foreign_rules_are_never_flushed(tmp_path,monkeypatch,rule):
    config,marker,calls=setup(tmp_path,monkeypatch,data=native([rule]))
    with pytest.raises(RuntimeError,match='неизвестные'):routes.apply(config)
    assert marker.exists() and not calls


def test_native_chain_is_not_mistaken_for_missing(monkeypatch):
    data=native();rows=[{'chain':data[0]}]+[{'rule':{**r,'chain':routes.CHAIN}} for r in data[1]]
    monkeypatch.setattr(routes.shutil,'which',lambda name:'/usr/sbin/nft')
    def run(*args,**kw):
        if args[0]=='iptables':return SimpleNamespace(returncode=1,stdout='',stderr='incompatible')
        return SimpleNamespace(returncode=0,stdout=json.dumps({'nftables':rows}))
    monkeypatch.setattr(routes,'run',run)
    kind,found=routes.chain_status()
    assert kind=='native' and found[0]['name']==routes.CHAIN


def test_failure_to_read_nft_is_not_treated_as_missing(monkeypatch):
    monkeypatch.setattr(routes.shutil,'which',lambda name:'/usr/sbin/nft')
    def run(*args,**kw):
        if args[-1]=='tables':raise RuntimeError('permission denied')
        return SimpleNamespace(returncode=1,stdout='',stderr='permission denied')
    monkeypatch.setattr(routes,'run',run)
    with pytest.raises(RuntimeError,match='permission'):routes.chain_status()


@pytest.mark.parametrize('owned',[True,False])
def test_chain_created_between_query_and_create_is_only_reused_if_owned(tmp_path,monkeypatch,owned):
    config,marker,calls=setup(tmp_path,monkeypatch,owned=owned)
    states=iter([('missing',None),('iptables',None)])
    monkeypatch.setattr(routes,'chain_status',lambda:next(states))
    original=routes.run
    def run(*args,**kw):
        if '-N' in args:
            calls.append((args,None));return SimpleNamespace(returncode=1,stderr='Chain already exists')
        return original(*args,**kw)
    monkeypatch.setattr(routes,'run',run)
    if owned:
        routes.apply(config);assert marker.exists()
    else:
        with pytest.raises(RuntimeError,match='чужие'):routes.apply(config)
        assert not marker.exists()
        assert not any('-A' in args or '-I' in args for args,_ in calls)


@pytest.mark.skipif(routes.fcntl is None,reason='Linux flock')
def test_processes_share_one_routing_lock(tmp_path,monkeypatch):
    script="""import runpy,sys,time
from pathlib import Path
m=runpy.run_path(sys.argv[1]);m['routing_lock'].__wrapped__.__globals__['STATE']=Path(sys.argv[2])
with m['routing_lock']():
 with open(sys.argv[3],'a') as f:f.write('enter\\n');f.flush()
 time.sleep(.15)
 with open(sys.argv[3],'a') as f:f.write('exit\\n');f.flush()
"""
    log=tmp_path/'events'
    args=[sys.executable,'-c',script,str(Path(routes.__file__)),str(tmp_path/'owned'),str(log)]
    processes=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(3)]
    for process in processes:
        _,error=process.communicate(timeout=10)
        assert process.returncode==0,error.decode()
    assert log.read_text().splitlines()==['enter','exit']*3


@pytest.mark.skipif(routes.fcntl is None,reason='Linux flock')
def test_lock_wait_is_bounded_and_released(tmp_path,monkeypatch):
    monkeypatch.setattr(routes,'STATE',tmp_path/'owned')
    with routes.routing_lock():
        with pytest.raises(RuntimeError,match='уже обновляется'):
            with routes.routing_lock(timeout=0):pass
    with routes.routing_lock(timeout=0):pass
