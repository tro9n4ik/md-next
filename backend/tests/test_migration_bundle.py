from contextlib import closing
import base64
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from app.services import migration_bundle as bundle

PASSWORD='Synthetic-Migration-Password-123'


@pytest.fixture
def source_tree(tmp_path):
    root=tmp_path/'source';app=root/'opt/md-next';(app/'backend').mkdir(parents=True)
    (app/'backend/VERSION').write_text('2.5.1')
    (app/'DEPLOYED_COMMIT').write_text('a'*40)
    for name in bundle.REQUIRED:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text('JWT_SECRET_KEY=synthetic-installation-key\n' if name.endswith('.env') else '{}')
    db=app/'backend/md_next.db'
    with closing(sqlite3.connect(db)) as c, c:
        c.executescript("CREATE TABLE alembic_version(version_num TEXT);INSERT INTO alembic_version VALUES ('0019');"
                        "CREATE TABLE users(id INTEGER,secret TEXT);INSERT INTO users VALUES(1,'private-password-hash');"
                        "CREATE TABLE client_groups(id INTEGER,name TEXT);INSERT INTO client_groups VALUES(2,'Demo');"
                        "CREATE TABLE clients(id INTEGER,uuid TEXT);INSERT INTO clients VALUES(3,'stable-client-uuid');"
                        "CREATE TABLE client_profiles(id INTEGER,auth TEXT);INSERT INTO client_profiles VALUES(4,'stable-profile-secret');")
    return root


def test_complete_encrypted_portable_roundtrip(source_tree,tmp_path):
    blob,manifest=bundle.capture(source_tree,PASSWORD,frozen=True)
    for secret in (b'stable-client-uuid',b'stable-profile-secret',b'synthetic-installation-key',b'private-password-hash'):
        assert secret not in blob
    restored,database,files=bundle.decode_bundle(blob,PASSWORD)
    assert restored==manifest and restored['frozen'] is True
    destination=tmp_path/'restored.db';destination.write_bytes(database)
    assert bundle.inventory_database(destination)=={k:manifest[k] for k in ('revision','tables')}
    with closing(sqlite3.connect(destination)) as c, c:
        assert c.execute('SELECT uuid FROM clients').fetchone()[0]=='stable-client-uuid'
        assert c.execute('SELECT auth FROM client_profiles').fetchone()[0]=='stable-profile-secret'
        assert c.execute('SELECT name FROM client_groups').fetchone()[0]=='Demo'
        assert c.execute('SELECT secret FROM users').fetchone()[0]=='private-password-hash'
    assert b'synthetic-installation-key' in files['opt/md-next/backend/.env']


def test_wrong_password_and_tampering(source_tree):
    blob,_=bundle.capture(source_tree,PASSWORD)
    with pytest.raises(ValueError):bundle.decode_bundle(blob,'another-password-123')
    changed=bytearray(blob);changed[-20]^=1
    with pytest.raises(ValueError):bundle.decode_bundle(bytes(changed),PASSWORD)


@pytest.mark.parametrize('name',['../etc/shadow','/etc/shadow','etc/letsencrypt/../shadow','etc/letsencrypt//x','etc\\shadow','opt/md-next/backend/app/main.py'])
def test_paths_are_not_executable_or_arbitrary(name):
    assert not bundle.allowed_path(name)
    with pytest.raises(ValueError):bundle.encode_bundle({},b'db',{name:b'bad'},PASSWORD)


def test_missing_configs_fail_before_export(source_tree):
    (source_tree/'opt/md-next/backend/.env').unlink()
    with pytest.raises(ValueError):bundle.capture(source_tree,PASSWORD)


def load_script(name):
    path=Path(__file__).resolve().parents[2]/'scripts'/name
    spec=importlib.util.spec_from_file_location(name.replace('.py','').replace('-','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_staging_preserves_secrets_and_clears_only_fence():
    runtime=load_script('migrate-panel.py')
    original=b'JWT_SECRET_KEY=keep-me\nSERVER_HOST=example.com\n'
    staging=runtime.staging_env(original,True)
    assert staging.count(b'MDNEXT_MIGRATION_STAGING=1')==1
    assert runtime.staging_env(staging,False)==original
    assert runtime.staging_env(staging,True)==staging


def test_atomic_writer_rejects_symlink(tmp_path):
    runtime=load_script('migrate-panel.py');original=tmp_path/'key';original.write_bytes(b'unchanged')
    link=tmp_path/'link'
    try:link.symlink_to(original)
    except OSError:pytest.skip('Symlink privilege unavailable')
    with pytest.raises((RuntimeError,OSError)):runtime.write(link,b'bad')
    assert original.read_bytes()==b'unchanged'


@pytest.mark.parametrize('field,value',[('host','127.0.0.1'),('host','10.0.0.1'),('host','224.0.0.1'),('host','metadata.google.internal'),('port',0),('port',True),('action','execute'),('fingerprint','untrusted'),('confirm',False),('password','short')])
def test_remote_request_rejects_unsafe_or_unconfirmed_input(field,value):
    worker=load_script('migration-ssh.py')
    request={'host':'8.8.8.8','port':22,'action':'prepare','fingerprint':'SHA256:'+'a'*43,'ssh_password':'synthetic-ssh','password':PASSWORD,'confirm':True}
    request[field]=value
    with pytest.raises((ValueError,TypeError)):worker.validate(request)


def test_activation_needs_external_test_confirmation():
    worker=load_script('migration-ssh.py')
    request={'host':'8.8.8.8','port':22,'action':'activate','fingerprint':'SHA256:'+'a'*43,'ssh_password':'synthetic-ssh','password':PASSWORD,'confirm':True}
    with pytest.raises(ValueError):worker.validate(request)
    request['confirm_protocol_tests']=True
    assert worker.validate(request)=='8.8.8.8'
