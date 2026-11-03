from contextlib import closing
import json
import shutil
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from app.services import migration_bundle as bundle
from test_migration_bundle import PASSWORD, load_script, source_tree


@pytest.fixture
def target(source_tree,tmp_path,monkeypatch):
    root=tmp_path/'target';shutil.copytree(source_tree,root)
    db=root/'opt/md-next/backend/md_next.db'
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('DELETE FROM clients');c.execute('DELETE FROM client_profiles')
    env=root/'opt/md-next/backend/.env';env.write_text(env.read_text()+'MDNEXT_MIGRATION_STAGING=1\n')
    runtime=load_script('migrate-panel.py')
    monkeypatch.setattr(runtime,'FILESYSTEM',root)
    monkeypatch.setattr(runtime,'ROOT',root/'opt/md-next')
    monkeypatch.setattr(runtime,'STATE',root/'var/lib/md-next/migration')
    monkeypatch.setattr(runtime,'library',lambda:bundle)
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda name:SimpleNamespace(pw_gid=0)))
    monkeypatch.setattr(runtime.os,'chown',lambda *args:None,raising=False)
    calls=[]
    def command(*args,**kwargs):
        calls.append(args)
        if args==('nginx','-V'):return SimpleNamespace(returncode=0,stdout='nginx/1.24.0 --with-http_v2_module',stderr='')
        return SimpleNamespace(returncode=3 if args[:2]==('systemctl','is-active') else 0,stdout='root\n')
    monkeypatch.setattr(runtime,'command',command)
    return runtime,root,calls


def test_trial_final_restore_and_rollback_preserve_identities(source_tree,target):
    runtime,root,calls=target
    trial,_=bundle.capture(source_tree,PASSWORD)
    runtime.restore(trial,PASSWORD)
    db=root/'opt/md-next/backend/md_next.db'
    with closing(sqlite3.connect(db)) as c, c:assert c.execute('SELECT uuid FROM clients').fetchone()[0]=='stable-client-uuid'
    env=(root/'opt/md-next/backend/.env').read_text()
    assert 'synthetic-installation-key' in env and 'MDNEXT_MIGRATION_STAGING=1' in env
    assert ('systemctl','start','md-next-mtproto') not in calls
    final,_=bundle.capture(source_tree,PASSWORD,frozen=True)
    runtime.restore(final,PASSWORD)
    assert json.loads((runtime.STATE/'restored.json').read_text())['frozen'] is True
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)
    with closing(sqlite3.connect(db)) as c, c:assert c.execute('SELECT count(*) FROM clients').fetchone()[0]==0
    assert not (runtime.STATE/'restored.json').exists()


def test_restore_refuses_existing_production_install(source_tree,target):
    runtime,root,calls=target
    env=root/'opt/md-next/backend/.env'
    env.write_bytes(runtime.staging_env(env.read_bytes(),False))
    blob,_=bundle.capture(source_tree,PASSWORD)
    with pytest.raises(RuntimeError,match='изолированную'):runtime.restore(blob,PASSWORD)
    assert not calls


def test_restore_failure_fences_services_and_keeps_rollback(source_tree,target,monkeypatch):
    runtime,root,calls=target
    blob,_=bundle.capture(source_tree,PASSWORD)
    original=runtime.command
    def fail(*args,**kwargs):
        if args==('nginx','-t'):raise RuntimeError('synthetic failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(runtime,'command',fail)
    with pytest.raises(RuntimeError,match='Службы остановлены'):runtime.restore(blob,PASSWORD)
    assert (runtime.STATE/'before-restore.mdmove').is_file()
    assert calls[-1]==('systemctl','stop','md-next-awg')
    monkeypatch.setattr(runtime,'command',original)
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)


def test_ssh_key_mismatch_never_sends_password(tmp_path,monkeypatch):
    import paramiko
    worker=load_script('migration-ssh.py');monkeypatch.setattr(worker,'STATE',tmp_path/'state')
    monkeypatch.setattr(worker,'load_runtime',lambda:SimpleNamespace(library=lambda:SimpleNamespace(capture=lambda *args,**kwargs:(b'',{}))))
    authenticated=[]
    class Transport:
        def __init__(self,sock):pass
        def start_client(self,**kwargs):pass
        def get_remote_server_key(self):return SimpleNamespace(asbytes=lambda:b'actual-server-key')
        def auth_password(self,*args):authenticated.append(args)
        def close(self):pass
    monkeypatch.setattr(paramiko,'Transport',Transport)
    monkeypatch.setattr(worker.socket,'create_connection',lambda *args,**kwargs:SimpleNamespace(close=lambda:None))
    request={'host':'8.8.8.8','port':22,'action':'prepare','fingerprint':'SHA256:'+'a'*43,'ssh_password':'synthetic-ssh-secret','password':PASSWORD,'confirm':True}
    with pytest.raises(RuntimeError,match='Пароль серверу не отправлен'):worker.run(request)
    assert authenticated==[]
    assert 'synthetic-ssh-secret' not in (worker.STATE/'job.json').read_text()
    assert PASSWORD not in (worker.STATE/'job.json').read_text()
