"""Управляемые ключи, подтверждение и порядок загрузки без реального переезда."""
from pathlib import Path
from types import SimpleNamespace
import pytest
import os
from test_migration_bundle import load_script


def root_session():
    module=load_script('migration-session.py')
    real=module.os
    def fstat(fd):
        info=real.fstat(fd)
        return SimpleNamespace(st_mode=info.st_mode,st_uid=0)
    module.os=SimpleNamespace(open=real.open,fdopen=real.fdopen,fstat=fstat,O_RDONLY=real.O_RDONLY,O_NOFOLLOW=getattr(real,'O_NOFOLLOW',0))
    return module


@pytest.mark.skipif(os.name!='posix',reason='Требуется проверка Unix-владельца и режима файла')
def test_managed_session_reuses_key_and_keeps_it_private(tmp_path):
    session=root_session();worker=load_script('migration-ssh.py')
    request={'action':'prepare','host':'8.8.8.8','port':22,'fingerprint':'SHA256:'+'a'*43,'ssh_password':'synthetic-password'}
    initial=session.hydrate(request,tmp_path,worker.write_json)
    assert len(initial['password'])>=32
    assert (tmp_path/'managed-session.json').stat().st_mode&0o077==0
    confirmed=session.hydrate({**request,'action':'commit','ssh_password':None},tmp_path,worker.write_json)
    assert confirmed['password']==initial['password']
    session.finish(tmp_path,worker.write_json)
    assert not (tmp_path/'managed-session.json').exists()
    recovered=session.read(tmp_path/'recovery-key.json')
    assert recovered=={'password':initial['password']}
    assert 'ssh_password' not in recovered


@pytest.mark.skipif(os.name!='posix',reason='Требуется проверка Unix-владельца и режима файла')
def test_managed_session_rejects_changed_binding(tmp_path):
    session=root_session();worker=load_script('migration-ssh.py')
    request={'action':'prepare','host':'8.8.8.8','port':22,'fingerprint':'SHA256:'+'a'*43,'ssh_password':'synthetic-password'}
    session.hydrate(request,tmp_path,worker.write_json)
    with pytest.raises(ValueError):session.hydrate({**request,'action':'commit','fingerprint':'SHA256:'+'b'*43},tmp_path,worker.write_json)
    with pytest.raises(ValueError):session.hydrate({**request,'fingerprint':'SHA256:'+'b'*43},tmp_path,worker.write_json)


def test_legacy_password_is_preserved(tmp_path):
    session=load_script('migration-session.py');request={'password':'legacy-secret-password','action':'cancel'}
    assert session.hydrate(request,tmp_path,lambda *a:pytest.fail('Не создавать новую сессию'))==request


@pytest.mark.parametrize('staging',[True,False])
def test_boot_setup_gates_awg_until_activation(tmp_path,staging):
    boot=load_script('boot-services.py');env=tmp_path/'opt/md-next/backend/.env';env.parent.mkdir(parents=True)
    env.write_text('MDNEXT_MIGRATION_STAGING=1\n' if staging else 'JWT_SECRET_KEY=synthetic\n')
    calls=[]
    def run(args,**kw):
        calls.append(args);return SimpleNamespace(returncode=0,stdout='loaded\n')
    boot.configure(tmp_path,run)
    marker=tmp_path/'var/lib/md-next/awg-boot-enabled'
    assert marker.exists()!=staging
    assert not any('start' in args or 'restart' in args for args in calls)
    unit=(tmp_path/'etc/systemd/system/md-next-awg.service').read_text()
    assert 'ConditionPathExists=/var/lib/md-next/awg-boot-enabled' in unit
    assert 'After=network-online.target xray.service' in unit
    assert any(len(args)>1 and args[1]=='enable' for args in calls)!=staging
    backend=(tmp_path/'etc/systemd/system/md-next-backend.service.d/40-md-next-boot-order.conf').read_text()
    assert ('xray.service' in backend)!=staging
    assert ('md-next-awg.service' in backend)!=staging


def test_commit_always_syncs_before_activation(monkeypatch):
    worker=load_script('migration-ssh.py');original=worker.run;calls=[]
    monkeypatch.setattr(worker,'validate',lambda request:None)
    monkeypatch.setattr(worker,'run',lambda request:calls.append(request['action']))
    original({'action':'commit'})
    assert calls==['finalize','activate']


def test_commit_does_not_activate_if_final_sync_fails(monkeypatch):
    worker=load_script('migration-ssh.py');original=worker.run;calls=[]
    monkeypatch.setattr(worker,'validate',lambda request:None)
    def run(request):calls.append(request['action']);raise RuntimeError('synthetic failure')
    monkeypatch.setattr(worker,'run',run)
    with pytest.raises(RuntimeError):original({'action':'commit'})
    assert calls==['finalize']
