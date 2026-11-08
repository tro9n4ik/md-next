"""Interrupted cutover, owned early cancellation and bounded network leases."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_migration_bundle import load_script, PASSWORD, source_tree  # noqa: F401
from test_migration_restore import target  # noqa: F401


@pytest.fixture
def latch(tmp_path,monkeypatch):
    if os.name!='posix':monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace())
    module=load_script('network-latch.py')
    module.STATE=tmp_path/'latch';module.STATE.mkdir()
    module.write=lambda path,text:path.write_text(text)
    module.replace=module.write
    module.folder=lambda token:module.STATE/token
    module.boot_id=lambda:'synthetic-boot'
    clock=[0.0];module.time=SimpleNamespace(monotonic=lambda:clock[0])
    module.shutil=SimpleNamespace(which=lambda name:None)
    calls=[]
    module.run=lambda *args,**kwargs:(calls.append((args,kwargs)) or SimpleNamespace(stdout='',stderr=''))
    return module,clock,calls


def test_live_lease_survives_long_verify_but_still_expires(latch):
    module,clock,calls=latch;token=module.arm()
    for now in range(100,1301,100):
        clock[0]=now;module.renew(token);module.tick(token)
        assert (module.folder(token)/'status').read_text()=='armed'
    clock[0]=1481;module.tick(token)
    assert (module.folder(token)/'status').read_text()=='restored'
    assert not (module.STATE/'active').exists()
    assert any('iptables-restore' in args for args,_ in calls)


def test_lease_cannot_be_renewed_past_hard_cap(latch):
    module,clock,_=latch;token=module.arm()
    for now in range(100,3600,100):clock[0]=now;module.renew(token)
    clock[0]=3590;module.renew(token)
    assert module.lease(module.folder(token))['deadline']==3600
    clock[0]=3600
    with pytest.raises(RuntimeError):module.renew(token)
    with pytest.raises(RuntimeError):module.confirm(token)
    module.tick(token)
    assert (module.folder(token)/'status').read_text()=='restored'


def test_expired_or_rebooted_lease_never_confirms(latch):
    module,clock,_=latch;token=module.arm();clock[0]=181
    with pytest.raises(RuntimeError):module.confirm(token)
    with pytest.raises(RuntimeError):module.renew(token)
    module.boot_id=lambda:'another-boot'
    with pytest.raises(RuntimeError):module.renew(token)


def test_old_confirmation_does_not_unlink_new_guard(latch):
    module,_,_=latch;token=module.arm();module.confirm(token)
    next_token=module.arm();module.confirm(token)
    assert (module.STATE/'active').read_text()==next_token


def test_failed_restore_retries_without_claiming_success(latch):
    module,clock,_=latch;token=module.arm();clock[0]=181
    original=module.run
    module.run=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('synthetic firewall error'))
    with pytest.raises(RuntimeError):module.tick(token)
    assert (module.folder(token)/'status').read_text()=='expired'
    assert (module.STATE/'active').exists()
    module.run=original;module.tick(token)
    assert (module.folder(token)/'status').read_text()=='restored'


def test_xt_backup_requires_complete_iptables_representation(latch):
    module,_,_=latch
    rows=[{'chain':{'family':'ip','table':'filter','name':'MDNEXT_AWG_OUT'}},
          {'rule':{'family':'ip','table':'filter','chain':'MDNEXT_AWG_OUT','expr':[{'xt':{'name':'REJECT'}}]}}]
    saved='*filter\n:MDNEXT_AWG_OUT - [0:0]\n-A MDNEXT_AWG_OUT -j REJECT --reject-with icmp-port-unreachable\nCOMMIT\n'
    module.run=lambda *a,**k:SimpleNamespace(stdout=json.dumps({'nftables':rows}) if a[:2]==('nft','-j') else saved,stderr='')
    module.compat_snapshots(module.STATE)
    assert (module.STATE/'compat-ip.rules').read_text()==saved
    rows.append({'chain':{'family':'ip','table':'filter','name':'foreign'}})
    with pytest.raises(RuntimeError,match='Смешанную'):module.compat_snapshots(module.STATE)


@pytest.fixture
def prepared(target):
    runtime,root,calls=target
    runtime.STATE.mkdir(parents=True,exist_ok=True)
    machine=root/'etc/machine-id';machine.parent.mkdir(parents=True,exist_ok=True);machine.write_text('b'*32)
    context={'session':'c'*32,'installation':hashlib.sha256(b'synthetic-installation-key').hexdigest(),
             'commit':'a'*40,'machine':'b'*32}
    (runtime.STATE/'prepare.json').write_text(json.dumps({'phase':'installed','commit':'a'*40,'source':context['installation']}))
    runtime.prepare_session(context)
    return runtime,root,calls,context


def test_early_cancel_requires_owned_empty_staging(prepared):
    runtime,root,calls,context=prepared
    runtime.cancel_target(context,PASSWORD)
    assert json.loads((runtime.STATE/'target-session.json').read_text())['phase']=='cancelled'
    assert not calls
    assert 'MDNEXT_MIGRATION_STAGING=1' in (root/'opt/md-next/backend/.env').read_text()


@pytest.mark.parametrize('state',['activated','importing.json','restored.json'])
def test_missing_baseline_never_allows_cancelling_started_restore(prepared,state):
    runtime,_,calls,context=prepared;(runtime.STATE/state).write_text('{}')
    with pytest.raises(RuntimeError):runtime.cancel_target(context,PASSWORD)
    assert not calls


def test_early_cancel_rejects_foreign_session_or_live_target(prepared):
    runtime,root,calls,context=prepared
    with pytest.raises(RuntimeError):runtime.cancel_target({**context,'session':'d'*32},PASSWORD)
    env=root/'opt/md-next/backend/.env';env.write_bytes(runtime.staging_env(env.read_bytes(),False))
    with pytest.raises(RuntimeError):runtime.cancel_target(context,PASSWORD)
    assert not calls


def test_cancel_after_restore_uses_original_backup(source_tree,prepared):
    from app.services import migration_bundle as bundle
    runtime,_,_,context=prepared
    blob,_=bundle.capture(source_tree,PASSWORD)
    runtime.restore(blob,PASSWORD)
    runtime.cancel_target(context,PASSWORD)
    assert not (runtime.STATE/'restored.json').exists()
    assert json.loads((runtime.STATE/'target-session.json').read_text())['phase']=='cancelled'


def test_installation_identity_is_independent_of_env_line_endings(source_tree):
    from app.services import migration_bundle as bundle
    env=source_tree/'opt/md-next/backend/.env'
    for newline in (b'\n',b'\r\n'):
        env.write_bytes(b'JWT_SECRET_KEY=synthetic-installation-key'+newline)
        _,manifest=bundle.capture(source_tree,PASSWORD)
        assert manifest['installation']==hashlib.sha256(b'synthetic-installation-key').hexdigest()


def test_cancel_restores_baseline_if_first_mutation_failed_before_import_marker(source_tree,prepared):
    from app.services import migration_bundle as bundle
    runtime,_,_,context=prepared;original=runtime.command
    def fail(*args,**kwargs):
        if args==('systemctl','disable','--now','certbot.timer'):return SimpleNamespace(returncode=1)
        return original(*args,**kwargs)
    runtime.command=fail
    blob,_=bundle.capture(source_tree,PASSWORD)
    with pytest.raises(RuntimeError):runtime.restore(blob,PASSWORD)
    assert not (runtime.STATE/'importing.json').exists()
    assert json.loads((runtime.STATE/'target-session.json').read_text())['phase']=='restoring'
    assert (runtime.STATE/'certbot-timer.json').exists()
    runtime.command=original;runtime.cancel_target(context,PASSWORD)
    assert not (runtime.STATE/'certbot-timer.json').exists()


def completed_receipt(prepared):
    runtime,root,calls,context=prepared
    context={**context,'snapshot':'d'*64}
    (runtime.STATE/'target-session.json').write_text(json.dumps({**context,'phase':'restored'}))
    (runtime.STATE/'restored.json').write_text(json.dumps({'installation':context['installation'],'frozen':True,'snapshot':context['snapshot']}))
    (runtime.STATE/'activation-receipt.json').write_text(json.dumps({**context,'phase':'completed'}))
    (runtime.STATE/'activated').write_text('Activated; rollback requires manual database reconciliation.\n')
    env=root/'opt/md-next/backend/.env';env.write_bytes(runtime.staging_env(env.read_bytes(),False))
    runtime.command=lambda *a,**k:(calls.append(a) or SimpleNamespace(returncode=0))
    return context


def test_completed_receipt_is_read_only_and_bound_to_frozen_snapshot(prepared):
    runtime,_,calls,_=prepared;context=completed_receipt(prepared)
    assert runtime.activation_result(context)['phase']=='completed'
    assert calls and all(a[:3]==('systemctl','is-active','--quiet') for a in calls)
    for key,size in [('session',32),('snapshot',64),('machine',32),('commit',40),('installation',64)]:
        with pytest.raises(RuntimeError):runtime.activation_result({**context,key:'e'*size})
    (runtime.STATE/'restored.json').write_text(json.dumps({'installation':context['installation'],'frozen':False,'snapshot':context['snapshot']}))
    with pytest.raises(RuntimeError):runtime.activation_result(context)


def test_incomplete_receipt_never_becomes_success(prepared):
    runtime,_,_,_=prepared;context=completed_receipt(prepared)
    (runtime.STATE/'activation-receipt.json').write_text(json.dumps({**context,'phase':'activating'}))
    assert runtime.activation_result(context)['phase']=='pending'


@pytest.mark.parametrize('action',['activate','verify'])
def test_cli_never_replays_activation_or_verifies_foreign_snapshot(prepared,monkeypatch,action):
    runtime,_,calls,_=prepared;context=completed_receipt(prepared)
    if action=='verify':context={**context,'snapshot':'e'*64}
    runtime.fcntl=SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *a:None)
    runtime.os=SimpleNamespace(geteuid=lambda:0)
    monkeypatch.setattr(sys,'argv',['migrate-panel.py',action,'--password-stdin','--confirm-cutover','--confirm-protocol-tests'])
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'password':PASSWORD,'context':context})))
    with pytest.raises(RuntimeError):runtime.main()
    assert not calls


def test_commit_history_uses_one_operation_and_failure_stage(tmp_path):
    worker=load_script('migration-ssh.py');worker.STATE=tmp_path
    request={'action':'finalize','host':'8.8.8.8','port':22,'operation_id':'a'*32}
    worker.status('restoring','Снимок',request)
    request={**request,'action':'activate'}
    worker.status('activating','Активация',request)
    worker.status('acknowledging','Подтверждение',request)
    worker.fail(TimeoutError('synthetic secret'),request)
    result=json.loads((tmp_path/'job.json').read_text())
    assert result['stage']=='acknowledging'
    assert [entry['stage'] for entry in result['history']][:3]==['restoring','activating','acknowledging']
    assert 'synthetic secret' not in json.dumps(result)


def test_reconciliation_never_calls_activate_or_resumes_source(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');worker.STATE=tmp_path
    saved={'session_id':'a'*32,'installation':'b'*64,'target_commit':'c'*40,'target_machine':'d'*32,'final_snapshot':'e'*64}
    (tmp_path/'source-activated.json').write_text(json.dumps({**saved,'guard':'f'*32,'state':'activation_started'}))
    request={'host':'8.8.8.8','port':22,'action':'reconcile','password':PASSWORD}
    closed=SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(worker,'connect_target',lambda *a,**k:(closed,closed))
    actions=[]
    def cli(transport,action,password,**kwargs):
        actions.append(action)
        return json.dumps({**kwargs['context'],'phase':'completed'})
    worker.remote_cli=cli
    worker.execute=lambda transport,command,**kwargs:('d'*32 if command=='cat /etc/machine-id' else json.dumps({'status':'armed','live':True}))
    confirmations=[];worker.confirm_network=lambda *a:confirmations.append(True)
    worker.finish_session=lambda:None
    worker.acknowledge_activation(request,saved)
    assert actions==['activation-status'] and confirmations==[True]
    assert json.loads((tmp_path/'job.json').read_text())['phase']=='activated'
    assert json.loads((tmp_path/'source-activated.json').read_text())['state']=='completed'


@pytest.mark.parametrize('phase,status',[('pending','armed'),('completed','restored'),('completed','expired')])
def test_ambiguous_receipt_or_expired_guard_never_completes(tmp_path,monkeypatch,phase,status):
    worker=load_script('migration-ssh.py');worker.STATE=tmp_path
    saved={'session_id':'a'*32,'installation':'b'*64,'target_commit':'c'*40,'target_machine':'d'*32,'final_snapshot':'e'*64}
    marker={**saved,'guard':'f'*32,'state':'activation_started'}
    (tmp_path/'source-activated.json').write_text(json.dumps(marker))
    closed=SimpleNamespace(close=lambda:None)
    worker.connect_target=lambda *a,**k:(closed,closed)
    worker.remote_cli=lambda *a,**k:json.dumps({**k['context'],'phase':phase})
    worker.execute=lambda transport,command,**kwargs:('d'*32 if command=='cat /etc/machine-id' else json.dumps({'status':status,'live':False}))
    worker.finish_session=lambda:pytest.fail('Do not close an ambiguous session')
    worker.confirm_network=lambda *a:pytest.fail('Do not confirm an expired guard')
    with pytest.raises(RuntimeError):worker.acknowledge_activation({'password':PASSWORD},saved)
    assert json.loads((tmp_path/'source-activated.json').read_text())==marker


def test_lost_reply_recovers_from_receipt_after_transient_disconnect(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');worker.STATE=tmp_path
    saved={'session_id':'a'*32,'installation':'b'*64,'target_commit':'c'*40,'target_machine':'d'*32,'final_snapshot':'e'*64}
    (tmp_path/'source-activated.json').write_text(json.dumps({**saved,'guard':'f'*32,'state':'activation_started'}))
    closed=SimpleNamespace(close=lambda:None);attempts=[]
    def connect(*a,**k):
        attempts.append(True)
        if len(attempts)==1:raise OSError('synthetic SSH disconnect')
        return closed,closed
    worker.connect_target=connect
    worker.time=SimpleNamespace(monotonic=lambda:0,sleep=lambda seconds:None,time=lambda:1)
    worker.remote_cli=lambda *a,**k:json.dumps({**k['context'],'phase':'completed'})
    worker.execute=lambda transport,command,**kwargs:('d'*32 if command=='cat /etc/machine-id' else json.dumps({'status':'confirmed','live':False}))
    worker.finish_session=lambda:None
    worker.confirm_network=lambda *a:pytest.fail('Already confirmed')
    worker.acknowledge_activation({'password':PASSWORD,'action':'activate','host':'8.8.8.8','port':22},saved,retry=True)
    assert len(attempts)==2
    assert json.loads((tmp_path/'job.json').read_text())['phase']=='activated'


def test_completed_source_receipt_recovers_lost_final_status(tmp_path,monkeypatch):
    control=load_script('migration-control.py');control.STATE=tmp_path
    control.subprocess=SimpleNamespace(run=lambda *a,**k:SimpleNamespace(returncode=3,stdout='inactive'))
    binding={'host':'8.8.8.8','port':22,'fingerprint':'SHA256:'+'a'*43,
             'session_id':'a'*32,'installation':'b'*64,'target_commit':'c'*40,'target_machine':'d'*32,'final_snapshot':'e'*64}
    (tmp_path/'target.json').write_text(json.dumps(binding))
    (tmp_path/'source-activated.json').write_text(json.dumps({**binding,'state':'completed'}))
    (tmp_path/'job.json').write_text('{invalid')
    status=control.read_status()
    assert status['phase']=='activated' and not status['can_reconcile']
    assert 'error_code' not in status
    assert 'final_snapshot' not in json.dumps(status)
    binding['final_snapshot']='f'*64
    (tmp_path/'target.json').write_text(json.dumps(binding))
    assert control.read_status()['phase']=='error'


def test_guard_renews_only_after_fresh_pinned_connection_and_identity(monkeypatch):
    worker=load_script('migration-ssh.py');calls=[]
    closed=SimpleNamespace(close=lambda:calls.append('closed'))
    worker.connect_target=lambda request,**kwargs:(closed,closed)
    def execute(transport,command,**kwargs):
        calls.append(command)
        return 'b'*32
    worker.execute=execute
    guard=worker.NetworkGuard({},'a'*32,'b'*32);guard.renew()
    assert calls[0]=='cat /etc/machine-id'
    assert ' renew ' in calls[1]
    calls.clear();guard.machine='c'*32
    with pytest.raises(RuntimeError):guard.renew()
    assert not any(' renew ' in c for c in calls)
    guard.error=TimeoutError('synthetic')
    with pytest.raises(RuntimeError):guard.check()


def test_failed_cancellation_fences_partially_started_source(tmp_path):
    worker=load_script('migration-ssh.py');worker.STATE=tmp_path/'state';worker.STATE.mkdir()
    worker.ROOT=tmp_path/'panel';env=worker.ROOT/'backend/.env';env.parent.mkdir(parents=True);env.write_bytes(b'JWT_SECRET_KEY=synthetic\n')
    (worker.STATE/'source-fenced.json').write_text('{}')
    calls=[]
    def command(*args,**kwargs):
        calls.append(args)
        if args==('systemctl','stop','md-next-awg'):raise TimeoutError('synthetic stop timeout')
    worker.load_runtime=lambda:SimpleNamespace(SERVICES=('md-next-awg','xray','md-next-backend'),command=command,
        staging_env=lambda content,enabled:content+b'MDNEXT_MIGRATION_STAGING=1\n',write=lambda path,blob,mode:path.write_bytes(blob))
    worker.keep_source_fenced()
    assert calls[:3]==[('systemctl','stop',service) for service in ('md-next-awg','xray','md-next-backend')]
    assert calls[-1]==('systemctl','start','md-next-backend')
    assert b'MDNEXT_MIGRATION_STAGING=1' in env.read_bytes()
    calls.clear();(worker.STATE/'source-activated.json').write_text('{}')
    worker.keep_source_fenced()
    assert not calls,'Never touch the source after activation started'
