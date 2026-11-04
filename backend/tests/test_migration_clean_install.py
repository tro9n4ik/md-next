"""Проверки восстановления данных поверх чистого билда без запуска переноса."""
import json
import hashlib
import tarfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_migration_bundle import load_script, PASSWORD, source_tree
from app.services import migration_bundle as bundle
from test_migration_restore import target  # noqa: F401


def test_clean_install_keeps_target_system_configuration(tmp_path,monkeypatch):
    runtime=load_script('migrate-panel.py')
    monkeypatch.setattr(runtime,'FILESYSTEM',tmp_path)
    site='etc/nginx/sites-available/md-next.conf'
    p=tmp_path/site;p.parent.mkdir(parents=True)
    p.write_text('server {\n ssl_certificate /new/cert;\n ssl_certificate_key /new/key;\n # clean build\n}')
    files={site:b'server {\n ssl_certificate /etc/letsencrypt/cert;\n ssl_certificate_key /etc/letsencrypt/key;\n # old build\n}',
           'etc/letsencrypt/cert':b'certificate','etc/letsencrypt/key':b'private-key',
           'etc/nginx/nginx.conf':b'old global configuration','etc/systemd/system/xray.service':b'old service',
           'opt/md-next/backend/.env':b'JWT_SECRET_KEY=source'}
    result=runtime.clean_install_files(files)
    assert b'clean build' in result[site] and b'old build' not in result[site]
    assert b'/etc/letsencrypt/cert' in result[site]
    assert 'etc/nginx/nginx.conf' not in result
    assert 'etc/systemd/system/xray.service' not in result
    assert result['opt/md-next/backend/.env']==files['opt/md-next/backend/.env']


def test_backup_mode_is_authenticated(source_tree):
    blob,_=bundle.capture(source_tree,PASSWORD,portable=True)
    manifest,_,_=bundle.decode_bundle(blob,PASSWORD)
    assert manifest['restore_mode']=='clean-install'
    assert manifest['components']=={}


def test_schema_upgrade_uses_disposable_database(tmp_path,monkeypatch):
    runtime=load_script('migrate-panel.py');calls=[]
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**kw:calls.append((a,kw)) or SimpleNamespace(returncode=0))
    path=tmp_path/'copy.db';runtime.upgrade_database(path)
    assert str(path) in calls[0][0][0][2]
    assert calls[0][0][0][-2:]==['upgrade','head']


def test_same_version_restore_rebinds_without_schema_upgrade(tmp_path,monkeypatch):
    runtime=load_script('migrate-panel.py');calls=[]
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**kw:calls.append((a,kw)) or SimpleNamespace(returncode=0))
    path=tmp_path/'copy.db'
    runtime.upgrade_database(path,b'JWT_SECRET_KEY=synthetic-restored-key\n',migrate=False)
    assert len(calls)==1
    assert calls[0][0][0][1]=='-c'
    assert calls[0][1]['env']['JWT_SECRET_KEY']=='synthetic-restored-key'


def test_latest_build_requires_signed_commit_and_both_workflows(monkeypatch):
    worker=load_script('migration-ssh.py');sha='a'*40
    def respond(request,**kw):
        url=request.full_url
        if 'workflows/ci.yml' in url:data={'workflow_runs':[{'head_sha':sha}]}
        elif '/commits/' in url:data={'commit':{'verification':{'verified':True}}}
        else:data={'workflow_runs':[{'path':p,'event':'push','conclusion':'success'} for p in ('.github/workflows/ci.yml','.github/workflows/secrets.yml')]}
        return BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(worker.urllib.request,'urlopen',respond)
    assert worker.latest_build()==sha


def test_failed_latest_security_check_blocks_candidate(monkeypatch):
    worker=load_script('migration-ssh.py');sha='a'*40
    def respond(request,**kw):
        url=request.full_url
        if 'workflows/ci.yml' in url:data={'workflow_runs':[{'head_sha':sha}]}
        elif '/commits/' in url:data={'commit':{'verification':{'verified':True}}}
        else:data={'workflow_runs':[
            {'id':1,'path':'.github/workflows/ci.yml','event':'push','conclusion':'success'},
            {'id':2,'path':'.github/workflows/secrets.yml','event':'push','conclusion':'success'},
            {'id':3,'path':'.github/workflows/secrets.yml','event':'push','conclusion':'failure'}]}
        return BytesIO(json.dumps(data).encode())
    monkeypatch.setattr(worker.urllib.request,'urlopen',respond)
    with pytest.raises(RuntimeError) as error:worker.latest_build()
    assert error.value.code=='MIG_BUILD'


def test_adguard_is_rebound_to_loopback_without_losing_rules():
    runtime=load_script('migration-components.py')
    import yaml
    config=yaml.safe_load(runtime.adguard_config(b'http: {address: "192.0.2.1:3001"}\ndns: {bind_hosts: ["192.0.2.1"], port: 53}\nuser_rules: ["||example.invalid^"]'))
    assert config['http']['address']=='127.0.0.1:3001'
    assert config['dns']['bind_hosts']==['127.0.0.1']
    assert config['dns']['port']==5354
    assert config['user_rules']==['||example.invalid^']


def test_adguard_dhcp_requires_explicit_manual_preparation():
    runtime=load_script('migration-components.py')
    with pytest.raises(ValueError):runtime.adguard_config(b'dns: {}\ndhcp: {enabled: true}')


def test_unfinished_import_cannot_be_overwritten_by_preparation(tmp_path,monkeypatch):
    driver=load_script('migration-target.py')
    monkeypatch.setattr(driver,'STATE',tmp_path)
    monkeypatch.setattr(driver,'ROOT',tmp_path/'empty-app')
    (tmp_path/'importing.json').write_text('{}')
    with pytest.raises(RuntimeError) as error:driver.check_empty_staging()
    assert error.value.code=='MIG_TARGET_OCCUPIED'


def test_portable_restore_upgrades_copy_keeps_clean_build_and_can_rollback(source_tree,target,monkeypatch):
    runtime,root,calls=target
    site='etc/nginx/sites-available/md-next.conf'
    source_site='server {\n ssl_certificate /etc/letsencrypt/live/demo/fullchain.pem;\n ssl_certificate_key /etc/letsencrypt/live/demo/privkey.pem;\n # source layout\n}'
    (source_tree/site).write_text(source_site)
    for name in ('fullchain.pem','privkey.pem'):
        p=source_tree/'etc/letsencrypt/live/demo'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic-certificate')
        p=root/'etc/nginx/ssl_dummy'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'clean-certificate')
    (root/site).write_text('server {\n ssl_certificate /etc/nginx/ssl_dummy/fullchain.pem;\n ssl_certificate_key /etc/nginx/ssl_dummy/privkey.pem;\n # clean build\n}')
    unit='etc/systemd/system/xray.service'
    for tree,content in ((source_tree,b'source unit'),(root,b'clean unit')):
        p=tree/unit;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
    (runtime.ROOT/'backend/VERSION').write_text('2.5.2')
    (runtime.ROOT/'DEPLOYED_COMMIT').write_text('b'*40)
    upgraded=[]
    monkeypatch.setattr(runtime,'upgrade_database',lambda path,*a:upgraded.append(path.read_bytes()))
    blob,_=bundle.capture(source_tree,PASSWORD,portable=True)
    runtime.restore(blob,PASSWORD)
    assert upgraded and b'clean build' in (root/site).read_bytes()
    assert (root/unit).read_bytes()==b'clean unit'
    assert (runtime.ROOT/'DEPLOYED_COMMIT').read_text()=='b'*40
    assert (runtime.STATE/'restored.json').exists()
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)
    assert b'clean build' in (root/site).read_bytes()
    assert not (root/'etc/letsencrypt/live/demo/privkey.pem').exists()
    assert not (runtime.STATE/'importing.json').exists()


def test_schema_failure_does_not_replace_target_database(source_tree,target,monkeypatch):
    runtime,root,calls=target
    database=root/'opt/md-next/backend/md_next.db';before=database.read_bytes()
    def incompatible(path,*a):raise runtime.events.MigrationError('MIG_SCHEMA')
    monkeypatch.setattr(runtime,'upgrade_database',incompatible)
    blob,_=bundle.capture(source_tree,PASSWORD,portable=True)
    with pytest.raises(RuntimeError) as error:runtime.restore(blob,PASSWORD)
    assert error.value.code=='MIG_SCHEMA'
    assert database.read_bytes()==before
    assert not calls


def test_adguard_rejects_checksum_mismatch(monkeypatch):
    runtime=load_script('migration-components.py')
    monkeypatch.setattr(runtime.platform,'machine',lambda:'x86_64')
    release={'assets':[{'name':'AdGuardHome_linux_amd64.tar.gz','digest':'sha256:'+'0'*64,
                       'browser_download_url':'https://github.com/AdguardTeam/AdGuardHome/releases/download/v0.107.79/AdGuardHome_linux_amd64.tar.gz'}]}
    monkeypatch.setattr(runtime,'request_bytes',lambda url,limit:json.dumps(release).encode() if 'api.github.com' in url else b'bad-archive')
    with pytest.raises(ValueError,match='сумма'):runtime.adguard_binary('v0.107.79')


@pytest.mark.parametrize('name',['AdGuardHome/AdGuardHome','./AdGuardHome/AdGuardHome'])
def test_adguard_reads_only_regular_binary_member(monkeypatch,name):
    runtime=load_script('migration-components.py')
    monkeypatch.setattr(runtime.platform,'machine',lambda:'x86_64')
    stream=BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as archive:
        entry=tarfile.TarInfo(name);entry.size=9;archive.addfile(entry,BytesIO(b'synthetic'))
        extra=tarfile.TarInfo('../../outside');extra.size=3;archive.addfile(extra,BytesIO(b'bad'))
    data=stream.getvalue()
    release={'assets':[{'name':'AdGuardHome_linux_amd64.tar.gz','digest':'sha256:'+hashlib.sha256(data).hexdigest(),
                       'browser_download_url':'https://github.com/AdguardTeam/AdGuardHome/releases/download/v0.107.79/AdGuardHome_linux_amd64.tar.gz'}]}
    monkeypatch.setattr(runtime,'request_bytes',lambda url,limit:json.dumps(release).encode() if 'api.github.com' in url else data)
    assert runtime.adguard_binary('v0.107.79')==b'synthetic'


def test_adguard_install_has_private_configuration_and_does_not_enable_service(tmp_path,monkeypatch):
    runtime=load_script('migration-components.py')
    p=tmp_path/'var/lib/md-next-adguard/AdGuardHome.yaml';p.parent.mkdir(parents=True);p.write_text('dns: {}')
    monkeypatch.setattr(runtime,'adguard_binary',lambda version:b'synthetic-binary')
    writes=[];calls=[]
    def command(*args,**kw):
        calls.append(args);return SimpleNamespace(returncode=1 if args[0]=='id' else 0)
    runtime.prepare_adguard({'components':{'adguard':{'version':'v0.107.79'}}},tmp_path,
                            lambda path,blob,mode:writes.append((path,mode)),command)
    assert (p,0o600) in writes
    assert (tmp_path/'opt/md-next-adguard/AdGuardHome',0o755) in writes
    assert ('systemctl','start','md-next-adguard') in calls
    assert not any('enable' in args for args in calls)


def test_final_restore_reuses_verified_adguard_binary(tmp_path,monkeypatch):
    runtime=load_script('migration-components.py')
    config=tmp_path/'var/lib/md-next-adguard/AdGuardHome.yaml';config.parent.mkdir(parents=True);config.write_text('dns: {}')
    binary=tmp_path/'opt/md-next-adguard/AdGuardHome';binary.parent.mkdir(parents=True);binary.write_bytes(b'checked-binary')
    marker=tmp_path/'var/lib/md-next/migration/adguard-owned';marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'version':'v0.107.79','sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}))
    monkeypatch.setattr(runtime,'adguard_binary',lambda *a:pytest.fail('Повторная загрузка при финальной синхронизации не нужна'))
    runtime.prepare_adguard({'components':{'adguard':{'version':'v0.107.79'}}},tmp_path,
                            lambda *a:None,lambda *a,**kw:SimpleNamespace(returncode=0))


@pytest.mark.parametrize("reconnect", [None, "target-machine", "different-machine"])
def test_preparation_finishes_before_source_backup(source_tree,tmp_path,monkeypatch,reconnect):
    import paramiko
    worker=load_script('migration-ssh.py')
    monkeypatch.setattr(worker,'ROOT',source_tree/'opt/md-next');monkeypatch.setattr(worker,'STATE',tmp_path/'state')
    env=worker.ROOT/'backend/.env';env.write_text(env.read_text()+'SERVER_HOST=vpn.example.com\nPANEL_PUBLIC_URL=https://panel.example.com\n')
    order=[]
    def capture(*a,**kw):
        order.append('backup');raise ValueError('synthetic stop before transfer')
    monkeypatch.setattr(worker,'load_runtime',lambda:SimpleNamespace(library=lambda:SimpleNamespace(capture=capture)))
    connection=SimpleNamespace(close=lambda:None)
    closed = []
    recovered = SimpleNamespace(close=lambda: closed.append("transport"))
    recovered_socket = SimpleNamespace(close=lambda: closed.append("socket"))
    connections = []

    def connect(request, **kwargs):
        connections.append(kwargs)
        if len(connections) == 1:
            return connection, connection
        return recovered_socket, recovered

    monkeypatch.setattr(worker,'connect_target',connect)
    monkeypatch.setattr(worker,'latest_build',lambda:'b'*40)
    sftp=SimpleNamespace(put=lambda *a:None,close=lambda:None)
    monkeypatch.setattr(paramiko.SFTPClient,'from_transport',lambda *a:sftp)
    read=worker.Path.read_text
    monkeypatch.setattr(worker.Path,'read_text',lambda path,*a,**kw:'source-machine' if path.as_posix()=='/etc/machine-id' else read(path,*a,**kw))
    cleanup = []

    def execute(transport,command,**kw):
        if 'machine-id' in command:
            return reconnect if transport is recovered else 'target-machine'
        if 'mktemp' in command:return '/root/mdnext-transfer-test'
        if command.startswith('bash '):order.append('clean-install')
        if command == 'rm -rf -- /root/mdnext-transfer-test':
            cleanup.append(transport)
            if transport is connection and reconnect:
                raise OSError("SSH disconnected")
        return ''
    monkeypatch.setattr(worker,'execute',execute)
    request={'action':'prepare','host':'8.8.8.8','port':22,'fingerprint':'SHA256:'+'a'*43,'ssh_password':'synthetic','password':PASSWORD,'email':'admin@example.com','confirm':True}
    with pytest.raises(RuntimeError):worker.run(request)
    assert order==['clean-install','backup']
    assert connections == ([{}, {"timeout": 5}] if reconnect else [{}])
    assert cleanup == ([connection, recovered] if reconnect == "target-machine" else [connection])
    assert closed == (["transport", "socket"] if reconnect else [])
