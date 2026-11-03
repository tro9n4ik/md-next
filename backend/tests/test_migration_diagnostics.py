"""Offline regression tests. Never contact a VPS or run an installer."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import sys
import os
import time

import pytest

from test_migration_bundle import load_script, PASSWORD, source_tree  # noqa: F401
from test_migration_restore import target  # noqa: F401


@pytest.mark.parametrize('line',[
    'password=never-show-me', 'MDNEXT_EVENT {"code":"UNKNOWN"}',
    'MDNEXT_EVENT {"code":[]}', 'MDNEXT_EVENT []',
    'MDNEXT_EVENT {"code":"INSTALL_AWG","password":"secret"}',
    'MDNEXT_EVENT '+('x'*1024), 'MDNEXT_EVENT invalid-json',
])
def test_remote_console_rejects_uncatalogued_output(line):
    events=load_script('migration-events.py')
    assert events.parse_event(line) is None


def test_events_never_forward_exception_details():
    events=load_script('migration-events.py')
    result=events.public_error(RuntimeError('password=private-key; token=abc'))
    assert result=={'code':'MIG_INTERNAL','message':events.MESSAGES['MIG_INTERNAL']}
    assert events.parse_event(events.event('INSTALL_AWG'))['message']==events.MESSAGES['INSTALL_AWG']


@pytest.mark.parametrize('host,valid',[
    ('vpn.example.com',True),('VPN.EXAMPLE.COM.',True),('vpn.рф',True),
    ('8.8.8.8',False),('::1',False),('',False),('vpn..example',False),
    ('http://vpn.example.com',False),('vpn.LOCALHOST',False),('x'*64+'.com',False),
])
def test_final_switch_requires_a_real_domain(host,valid):
    worker=load_script('migration-ssh.py')
    assert worker.public_domain(host)==valid


def test_orphaned_credentials_are_cleaned_even_without_job_state(tmp_path,monkeypatch):
    control=load_script('migration-control.py');monkeypatch.setattr(control,'STATE',tmp_path)
    monkeypatch.setattr(control.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=3,stdout='inactive'))
    request=tmp_path/('request-'+'a'*32+'.json');request.write_text('{"password":"sensitive"}')
    os.utime(request,(time.time()-120,time.time()-120))
    assert control.read_status()['phase']=='idle'
    assert not request.exists()
    (tmp_path/'job.json').write_text('not-json')
    assert control.read_status()['error_code']=='MIG_STATE'


def test_starting_unit_is_not_reported_as_interrupted(tmp_path,monkeypatch):
    control=load_script('migration-control.py');monkeypatch.setattr(control,'STATE',tmp_path)
    monkeypatch.setattr(control.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=3,stdout='activating'))
    (tmp_path/'job.json').write_text(json.dumps({'phase':'preparing','updated_at':time.time()-100}))
    assert control.read_status()['phase']=='preparing'


def test_ssh_stream_is_bounded_and_exports_only_safe_steps(monkeypatch):
    worker=load_script('migration-ssh.py');chunks=[
        b'INITIAL_ADMIN_PASSWORD=secret\n'+b'x'*70000,
        b'\nMDNEXT_EVENT {"code":"INSTALL_AWG"}\n',
        b'MDNEXT_EVENT {"code":"MIG_APT_LOCK"}\n',
    ]
    class Channel:
        def settimeout(self,n):pass
        def exec_command(self,n):pass
        def shutdown_write(self):pass
        def recv_ready(self):return bool(chunks)
        def recv(self,n):return chunks.pop(0)
        def recv_stderr_ready(self):return False
        def exit_status_ready(self):return not chunks
        def recv_exit_status(self):return 1
        def close(self):pass
    monkeypatch.setattr(worker.time,'sleep',lambda n:None)
    history=[]
    with pytest.raises(RuntimeError) as error:
        worker.execute(SimpleNamespace(open_session=lambda **kw:Channel()),'fixed-command',progress=history.append)
    assert error.value.code=='MIG_APT_LOCK'
    assert [row['code'] for row in history]==['INSTALL_AWG']
    assert 'secret' not in json.dumps(history)


def test_error_status_preserves_stage_without_secrets(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');monkeypatch.setattr(worker,'STATE',tmp_path)
    request={'host':'8.8.8.8','port':22,'action':'prepare','password':PASSWORD,'ssh_password':'secret'}
    worker.status('preparing','catalogue message',request,stage='INSTALL_AWG')
    worker.fail(worker.events.MigrationError('MIG_INSTALL',exit_code=1),request)
    data=json.loads((tmp_path/'job.json').read_text())
    assert data['phase']=='error' and data['stage']=='INSTALL_AWG' and data['error_code']=='MIG_INSTALL'
    assert data['exit_code']==1 and len(data['history'])==2
    assert PASSWORD not in json.dumps(data) and 'secret' not in json.dumps(data)
    for index in range(100):worker.status('preparing',str(index),request)
    assert len(json.loads((tmp_path/'job.json').read_text())['history'])==80


def test_reboot_is_a_waiting_state(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');monkeypatch.setattr(worker,'STATE',tmp_path)
    request={'host':'8.8.8.8','port':22,'action':'prepare'}
    worker.fail(worker.events.MigrationError('MIG_REBOOT_REQUIRED'),request)
    assert json.loads((tmp_path/'job.json').read_text())['phase']=='reboot_required'


def test_os_update_precedes_install_and_reboot_blocks_it(tmp_path,monkeypatch):
    driver=load_script('migration-target.py');monkeypatch.setattr(driver,'STATE',tmp_path)
    monkeypatch.setattr(driver,'preflight',lambda *args:None)
    monkeypatch.setattr(driver,'wait_apt',lambda:None)
    monkeypatch.setattr(driver,'boot_id',lambda:'before-reboot')
    calls=[]
    monkeypatch.setattr(driver,'run',lambda command,*args,**kwargs:calls.append(command))
    monkeypatch.setattr(driver.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=3))
    original=driver.Path.exists
    monkeypatch.setattr(driver.Path,'exists',lambda path:True if str(path).replace('\\','/')=='/var/run/reboot-required' else original(path))
    with pytest.raises(RuntimeError) as error:
        driver.prepare('a'*40,'vpn.example.com','panel.example.com','demo@example.com','b'*64)
    assert error.value.code=='MIG_REBOOT_REQUIRED'
    assert [cmd[-2:] for cmd in calls]==[['update','-qq'],['-y','-qq']]
    assert not any(cmd[0] in {'git','bash'} for cmd in calls)
    assert json.loads((tmp_path/'prepare.json').read_text())['phase']=='reboot_required'


def test_clearing_reboot_file_is_not_a_reboot(tmp_path,monkeypatch):
    driver=load_script('migration-target.py');monkeypatch.setattr(driver,'STATE',tmp_path)
    monkeypatch.setattr(driver,'preflight',lambda *args:{'phase':'reboot_required','boot_id':'same-boot'})
    monkeypatch.setattr(driver,'boot_id',lambda:'same-boot')
    monkeypatch.setattr(driver,'run',lambda *args,**kwargs:pytest.fail('No command may start before reboot'))
    with pytest.raises(RuntimeError) as error:driver.prepare('a'*40,'vpn.example.com','panel.example.com','demo@example.com','b'*64)
    assert error.value.code=='MIG_REBOOT_REQUIRED'


def test_foreign_or_changed_source_cannot_resume():
    driver=load_script('migration-target.py')
    assert driver.marker_matches({'commit':'a'*40,'source':'b'*64},'a'*40,'b'*64)
    assert not driver.marker_matches({'commit':'a'*40,'source':'c'*64},'a'*40,'b'*64)
    assert not driver.marker_matches(None,'a'*40,'b'*64)


def test_wait_for_package_manager_has_a_deadline(monkeypatch):
    driver=load_script('migration-target.py')
    monkeypatch.setattr(driver,'apt_unlocked',lambda:False)
    clock=iter([0,901]);monkeypatch.setattr(driver.time,'monotonic',lambda:next(clock))
    with pytest.raises(RuntimeError) as error:driver.wait_apt()
    assert error.value.code=='MIG_APT_LOCK'


@pytest.mark.skipif(os.name!='posix',reason='Linux process group and root log semantics')
def test_private_target_log_does_not_forward_bootstrap_password(tmp_path,monkeypatch):
    driver=load_script('migration-target.py');monkeypatch.setattr(driver,'STATE',tmp_path)
    public=[];monkeypatch.setattr(driver,'emit',public.append)
    code="print('bootstrap-password-do-not-forward'); print('MDNEXT_EVENT {\"code\":\"INSTALL_AWG\"}')"
    driver.run([sys.executable,'-c',code],'MIG_INSTALL',timeout=10)
    assert public==['INSTALL_AWG']
    assert (tmp_path/'prepare.log').stat().st_mode & 0o777==0o600
    assert 'bootstrap-password-do-not-forward' in (tmp_path/'prepare.log').read_text()


def test_missing_awg_draft_is_permitted_only_in_staging(tmp_path,monkeypatch):
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace())
    installer=load_script('install-privilege-separation.py')
    monkeypatch.setattr(installer.os,'O_NOFOLLOW',0,raising=False)
    source=tmp_path/'runtime'/'awg0.conf';draft=tmp_path/'awg0.conf'
    with pytest.raises(RuntimeError):installer.initial_config(source,draft,staging=False)
    assert not draft.exists()
    installer.initial_config(source,draft,staging=True)
    assert draft.read_bytes()==b'' and not source.exists()
    draft.write_bytes(b'preserved-configuration')
    installer.initial_config(source,draft,staging=True)
    assert draft.read_bytes()==b'preserved-configuration'
    with pytest.raises(RuntimeError):installer.initial_config(tmp_path/'xray.json',tmp_path/'draft.json',staging=True)


def test_staging_resume_refuses_imported_clients(tmp_path,monkeypatch):
    driver=load_script('migration-target.py');root=tmp_path/'app';(root/'backend').mkdir(parents=True)
    monkeypatch.setattr(driver,'ROOT',root);monkeypatch.setattr(driver,'STATE',tmp_path/'state')
    (root/'backend/.env').write_text('MDNEXT_MIGRATION_STAGING=1\n')
    with closing(sqlite3.connect(root/'backend/md_next.db')) as db,db:
        db.executescript('CREATE TABLE clients(id INTEGER); INSERT INTO clients VALUES(1);')
    with pytest.raises(RuntimeError) as error:driver.check_empty_staging()
    assert error.value.code=='MIG_TARGET_OCCUPIED'


def test_database_digest_observes_committed_wal(target):
    runtime,root,calls=target
    with closing(sqlite3.connect(root/'opt/md-next/backend/md_next.db')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        before=runtime.database_digest()
        assert runtime.database_digest()==before
        db.execute("INSERT INTO clients VALUES(99,'new-client')");db.commit()
        assert runtime.database_digest()!=before


def test_config_digest_invalidates_verification_when_keys_change(target):
    runtime,root,calls=target
    before=runtime.configuration_digest()
    env=root/'opt/md-next/backend/.env';env.write_text(env.read_text()+'CHANGED_KEY=synthetic\n')
    assert runtime.configuration_digest()!=before


def test_restore_must_stop_services_before_replacing_database(source_tree,target,monkeypatch):
    runtime,root,calls=target;db=root/'opt/md-next/backend/md_next.db';before=db.read_bytes()
    blob,_=runtime.library().capture(source_tree,PASSWORD)
    original=runtime.command
    def active(*args,**kwargs):
        if args[:2]==('systemctl','is-active'):return SimpleNamespace(returncode=0)
        return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'command',active)
    with pytest.raises(RuntimeError):runtime.restore(blob,PASSWORD)
    assert db.read_bytes()==before


def test_partial_restore_invalidates_verification_and_can_rollback(source_tree,target,monkeypatch):
    runtime,root,calls=target;blob,_=runtime.library().capture(source_tree,PASSWORD)
    original=runtime.command
    def broken(*args,**kwargs):
        if args==('nginx','-t'):raise RuntimeError('synthetic failure')
        return original(*args,**kwargs)
    runtime.STATE.mkdir(parents=True)
    (runtime.STATE/'verified.json').write_text('{"old":true}')
    monkeypatch.setattr(runtime,'command',broken)
    with pytest.raises(RuntimeError):runtime.restore(blob,PASSWORD)
    assert not (runtime.STATE/'verified.json').exists()
    assert (runtime.STATE/'importing.json').exists()
    monkeypatch.setattr(runtime,'command',original)
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)
    assert not (runtime.STATE/'importing.json').exists()
    # Cancellation is retryable if the source restart failed after target rollback.
    assert (runtime.STATE/'before-restore.mdmove').exists()
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)
