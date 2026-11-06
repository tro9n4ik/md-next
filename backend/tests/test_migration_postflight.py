import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from test_migration_bundle import load_script, source_tree
from test_migration_restore import target


def certificate_files():
    live = 'etc/letsencrypt/live/example.org/'
    archive = 'etc/letsencrypt/archive/example.org/'
    files = {'etc/letsencrypt/renewal/example.org.conf': (
        'archive_dir = /etc/letsencrypt/archive/example.org\n' +
        ''.join(f'{p} = /{live}{p}.pem\n' for p in ('cert','chain','fullchain','privkey')) +
        '\n[renewalparams]\nauthenticator = webroot\n').encode()}
    for part in ('cert','chain','fullchain','privkey'):
        files[live+part+'.pem'] = (part+'-current').encode()
        files[archive+part+'1.pem'] = (part+'-old').encode()
        files[archive+part+'2.pem'] = files[live+part+'.pem']
    return files


def test_certbot_uses_matching_complete_generation():
    module = load_script('migration-postflight.py')
    files = certificate_files()
    plan = module.certbot_link_plan(files)
    assert len(plan) == 4
    assert set(plan.values()) == {f'etc/letsencrypt/archive/example.org/{p}2.pem'
                                  for p in ('cert','chain','fullchain','privkey')}
    files['etc/letsencrypt/archive/example.org/privkey2.pem'] = b'wrong'
    with pytest.raises(ValueError, match='не соответствует'):
        module.certbot_link_plan(files)


@pytest.mark.parametrize('path', ['/tmp/foreign', '/etc/letsencrypt/archive/../escape'])
def test_certbot_rejects_foreign_layout(path):
    module = load_script('migration-postflight.py')
    files = certificate_files()
    name = 'etc/letsencrypt/renewal/example.org.conf'
    files[name] = files[name].replace(b'/etc/letsencrypt/archive/example.org', path.encode())
    with pytest.raises(ValueError):
        module.certbot_link_plan(files)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX links')
def test_certbot_links_are_relative_idempotent_and_confined(tmp_path):
    module = load_script('migration-postflight.py')
    files = certificate_files()
    for name, data in files.items():
        path = tmp_path/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    plan = module.certbot_link_plan(files)
    for _ in range(2):
        module.restore_certbot_links(tmp_path, plan)
        for name, target in plan.items():
            path = tmp_path/name
            assert path.is_symlink() and not path.readlink().is_absolute()
            assert path.resolve() == tmp_path/target


def test_target_dns_rebinds_only_enabled_adguard_and_keeps_other_settings(tmp_path):
    module = load_script('migration-postflight.py')
    path = tmp_path/'db'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);"
                        "INSERT INTO settings VALUES('adguard.enabled','true');"
                        "INSERT INTO settings VALUES('adguard.bootstrap_ip','88.210.35.93');"
                        "INSERT INTO settings VALUES('unrelated','keep');")
    for address in ('195.78.48.60', '2001:db8::1'):
        module.rebind_dns(path, address)
        with sqlite3.connect(path) as db:
            assert dict(db.execute('SELECT key,value FROM settings')) == {
                'adguard.enabled':'true','adguard.bootstrap_ip':address,'unrelated':'keep'}
    with pytest.raises(ValueError): module.rebind_dns(path, None)
    with pytest.raises(ValueError): module.rebind_dns(path, 'bad-address')
    with sqlite3.connect(path) as db: db.execute("UPDATE settings SET value='false' WHERE key='adguard.enabled'")
    module.rebind_dns(path, None)


def test_logrotate_keeps_one_owner_and_preserves_foreign_config(tmp_path):
    module = load_script('install-security.py')
    directory = tmp_path/'etc/logrotate.d'; directory.mkdir(parents=True)
    module.configure_auth_logrotate(tmp_path)
    own = directory/'md-next-auth'
    assert own.read_text() == module.AUTH_LOGROTATE
    nginx = directory/'nginx'; original = '/var/log/nginx/*.log {\n daily\n}\n'
    nginx.write_text(original)
    for _ in range(2):module.configure_auth_logrotate(tmp_path)
    assert not own.exists() and nginx.read_text() == original
    own.write_text('custom config')
    with pytest.raises(RuntimeError, match='Нестандартный'):
        module.configure_auth_logrotate(tmp_path)
    assert own.read_text() == 'custom config'


def dns_fixture(tmp_path, monkeypatch):
    module = load_script('migration-dns.py')
    monkeypatch.setattr(module, 'CONFIG', tmp_path/'dns/verified.conf')
    monkeypatch.setattr(module.subprocess, 'run', lambda *a,**kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(module, 'configured_servers', lambda:['192.0.2.1'])
    monkeypatch.setattr(module, 'run', lambda *a,**kw: SimpleNamespace(stdout=''))
    monkeypatch.setattr(module, 'system_check', lambda hosts:None)
    return module


def test_working_dns_is_not_replaced(tmp_path, monkeypatch):
    module = dns_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'bounded_probe', lambda *a:None)
    assert module.ensure_dns(repair=True) is False
    assert not module.CONFIG.exists()


def test_refused_provider_is_replaced_only_after_dot_probes(tmp_path, monkeypatch):
    module = dns_fixture(tmp_path, monkeypatch); calls=[]
    def probe(server,host):
        calls.append((server,host))
        if server == '192.0.2.1':raise ValueError('REFUSED')
    monkeypatch.setattr(module, 'bounded_probe', probe)
    assert module.ensure_dns(repair=True) is True
    assert 'DNSOverTLS=yes' in module.CONFIG.read_text()
    assert all((s,h) in calls for s in module.PROVIDERS for h in module.HOSTS)


def test_dns_failed_postcheck_restores_previous_config(tmp_path, monkeypatch):
    module = dns_fixture(tmp_path, monkeypatch)
    module.CONFIG.parent.mkdir();module.CONFIG.write_text('old configuration')
    monkeypatch.setattr(module, 'bounded_probe', lambda s,h: (_ for _ in ()).throw(ValueError()) if s=='192.0.2.1' else None)
    attempts=[]
    def check(hosts):
        attempts.append(True)
        if len(attempts)>1:raise RuntimeError('Post-check failed')
    monkeypatch.setattr(module, 'system_check', check)
    with pytest.raises(RuntimeError):module.ensure_dns(repair=True)
    assert module.CONFIG.read_text() == 'old configuration'


def test_bad_dns_check_never_mutates_without_repair(tmp_path, monkeypatch):
    module = dns_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'bounded_probe', lambda *a: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(RuntimeError): module.ensure_dns(repair=False)
    assert not module.CONFIG.exists()


def test_unreachable_encrypted_provider_does_not_replace_config(tmp_path, monkeypatch):
    module = dns_fixture(tmp_path, monkeypatch)
    module.CONFIG.parent.mkdir();module.CONFIG.write_text('original')
    monkeypatch.setattr(module, 'bounded_probe', lambda *a: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(RuntimeError):module.ensure_dns(repair=True)
    assert module.CONFIG.read_text() == 'original'


def test_dns_probe_rejects_refused_and_mismatched_wire_reply(monkeypatch):
    import struct
    module = load_script('migration-dns.py')
    response = [struct.pack('!HHHHHH', 0, 0x8185, 1, 1, 0, 0)]
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def settimeout(self,*a):pass
        def connect(self,*a):pass
        def send(self,data):self.query=data
        def recv(self,*a):return self.query[:2]+response[0][2:]
    monkeypatch.setattr(module.socket,'socket',lambda *a:Socket())
    with pytest.raises(ValueError):module.probe('192.0.2.1','example.org')
    response[0] = struct.pack('!HHHHHH', 0, 0x8180, 1, 1, 0, 0)
    module.probe('192.0.2.1','example.org')
    response[0] = b'short'
    with pytest.raises(ValueError):module.probe('192.0.2.1','example.org')


def test_dns_children_have_hard_timeouts(monkeypatch):
    module = load_script('migration-dns.py');calls=[]
    monkeypatch.setattr(module,'run',lambda args,timeout=8:calls.append((args,timeout)))
    module.bounded_probe('192.0.2.1','example.org')
    assert calls[0][1] == 6 and '--probe' in calls[0][0]


def test_invalid_certbot_bundle_fails_before_target_mutation(source_tree,target):
    from test_migration_bundle import PASSWORD
    runtime, root, calls = target
    files = certificate_files()
    files['etc/letsencrypt/archive/example.org/privkey2.pem'] = b'mismatched'
    for name, content in files.items():
        path=source_tree/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    blob,_=runtime.library().capture(source_tree,PASSWORD)
    db=root/'opt/md-next/backend/md_next.db';before=db.read_bytes()
    with pytest.raises(RuntimeError) as error:runtime.restore(blob,PASSWORD)
    assert error.value.code == 'MIG_CERTBOT_LAYOUT'
    assert db.read_bytes() == before and not calls


@pytest.mark.skipif(os.name != 'posix',reason='POSIX links')
def test_certbot_restore_repeat_and_cancel_remove_imported_links(source_tree,target):
    from test_migration_bundle import PASSWORD
    runtime, root, _ = target
    for name,content in certificate_files().items():
        path=source_tree/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    blob,_=runtime.library().capture(source_tree,PASSWORD)
    runtime.restore(blob,PASSWORD)
    runtime.restore(blob,PASSWORD)
    assert (root/'etc/letsencrypt/live/example.org/cert.pem').is_symlink()
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)
    assert not list((root/'etc/letsencrypt/live/example.org').glob('*.pem'))


def test_dns_uses_only_available_authenticated_providers(tmp_path,monkeypatch):
    module=dns_fixture(tmp_path,monkeypatch)
    def probe(server,host):
        if server in ['192.0.2.1',module.PROVIDERS[0]]:raise TimeoutError()
    monkeypatch.setattr(module,'bounded_probe',probe)
    assert module.ensure_dns(repair=True)
    config=module.CONFIG.read_text()
    assert module.PROVIDERS[1] in config and module.PROVIDERS[0] not in config


def test_certbot_hook_modes_are_scoped_and_legacy_managed_hook_is_supported():
    module=load_script('migration-postflight.py')
    known='etc/letsencrypt/renewal-hooks/deploy/md-next-xray-certificate.sh'
    custom='etc/letsencrypt/renewal-hooks/post/custom.sh'
    files={known:b'synthetic',custom:b'synthetic'}
    assert module.certbot_executable_plan({known:files[known]}, {})=={known}
    assert module.certbot_executable_plan(files,{'certbot_executables':[custom]})=={custom}
    with pytest.raises(ValueError):module.certbot_executable_plan(files,{})
    for paths in [['etc/shadow'],['etc/letsencrypt/renewal-hooks/../escape'],[1],'bad']:
        with pytest.raises(ValueError):module.certbot_executable_plan(files,{'certbot_executables':paths})


@pytest.mark.skipif(os.name!='posix',reason='Executable bits')
def test_capture_preserves_certbot_hook_executable_flag(source_tree):
    from test_migration_bundle import PASSWORD
    from app.services import migration_bundle
    hook='etc/letsencrypt/renewal-hooks/deploy/custom.sh'
    path=source_tree/hook;path.parent.mkdir(parents=True);path.write_text('#!/bin/sh\ntrue\n');path.chmod(0o755)
    blob,manifest=migration_bundle.capture(source_tree,PASSWORD)
    decoded,_,_=migration_bundle.decode_bundle(blob,PASSWORD)
    assert manifest['certbot_executables']==decoded['certbot_executables']==[hook]


def test_standalone_renewal_moves_to_known_webroot_without_stopping_nginx():
    module=load_script('migration-postflight.py');files=certificate_files()
    name='etc/letsencrypt/renewal/example.org.conf'
    files[name]=files[name].replace(b'authenticator = webroot',b'authenticator = standalone')
    files['etc/nginx/sites-available/md-next.conf']=b'server { location /.well-known/acme-challenge/ { root /var/www/html; } }'
    updated=module.renewal_files(files)
    assert b'authenticator = webroot' in updated[name] and b'webroot_path = /var/www/html,' in updated[name]
    assert module.renewal_files(updated)==updated
    files[name]+=b'pre_hook = custom-command\n'
    with pytest.raises(ValueError):module.renewal_files(files)


def test_certbot_timer_stays_fenced_until_cancel(source_tree,target):
    from test_migration_bundle import PASSWORD
    runtime,root,calls=target;blob,_=runtime.library().capture(source_tree,PASSWORD)
    runtime.restore(blob,PASSWORD)
    assert ('systemctl','disable','--now','certbot.timer') in calls
    assert ('systemctl','start','certbot.timer') not in calls
    assert (runtime.STATE/'certbot-timer.json').exists()
    runtime.restore((runtime.STATE/'before-restore.mdmove').read_bytes(),PASSWORD,rollback=True)
    assert not (runtime.STATE/'certbot-timer.json').exists()


def test_private_dns_domains_are_not_overwritten(tmp_path,monkeypatch):
    module=dns_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(module,'bounded_probe',lambda *a: (_ for _ in ()).throw(ValueError()))
    monkeypatch.setattr(module,'custom_dns_domains',lambda:True)
    with pytest.raises(RuntimeError,match='Нестандартные'):module.ensure_dns(repair=True)
    assert not module.CONFIG.exists()


def test_public_dns_checks_respect_global_routing_scope(monkeypatch):
    module=load_script('migration-dns.py')
    values={'dns':'Global: 8.8.8.8#dns.google\nLink 2 (eth0): 192.0.2.1\n',
            'domain':'Global: ~.\nLink 2 (eth0):\n'}
    monkeypatch.setattr(module,'run',lambda args,**kw:SimpleNamespace(stdout=values[args[1]]))
    assert module.configured_servers()==['8.8.8.8#dns.google']
    values['domain']='Global:\nLink 2 (eth0):\n'
    assert set(module.configured_servers())=={'8.8.8.8#dns.google','192.0.2.1'}


def test_static_dns_is_checked_but_not_automatically_replaced(tmp_path,monkeypatch):
    module=dns_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=3))
    monkeypatch.setattr(module,'static_servers',lambda:['192.0.2.1'])
    monkeypatch.setattr(module,'bounded_probe',lambda *a: (_ for _ in ()).throw(ValueError('REFUSED')))
    with pytest.raises(RuntimeError):module.ensure_dns(repair=True)
    assert not module.CONFIG.exists()
