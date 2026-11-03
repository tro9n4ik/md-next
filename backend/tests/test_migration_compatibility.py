"""Compatibility and reboot regressions; no servers or installers are started."""
import json
from types import SimpleNamespace

import pytest

from test_migration_bundle import load_script


@pytest.mark.parametrize('version',[(1,24,0),(1,22,1),(1,25,0)])
def test_modern_http2_converts_for_old_nginx_without_changing_tls(version):
    runtime=load_script('migrate-panel.py')
    source='''# keep http2 on; in this comment
server {
    listen 127.0.0.1:8080 ssl proxy_protocol;
    listen [::1]:8443 ssl proxy_protocol;
    http2 on;
    ssl_certificate /etc/example/cert.pem;
    location / { add_header X-Debug "http2 on; { quoted }"; proxy_pass http://127.0.0.1:8000; }
}
server { listen 80; http2 off; return 301 https://$host$request_uri; }
'''
    result=runtime.normalize_nginx_http2(source,version)
    assert 'listen 127.0.0.1:8080 ssl proxy_protocol http2;' in result
    assert 'listen [::1]:8443 ssl proxy_protocol http2;' in result
    assert 'server { listen 80;  return 301 https://$host$request_uri; }' in result
    assert 'ssl_certificate /etc/example/cert.pem;' in result
    assert 'add_header X-Debug "http2 on; { quoted }";' in result
    assert '# keep http2 on; in this comment' in result
    assert runtime.normalize_nginx_http2(result,version)==result


@pytest.mark.parametrize('version',[(1,25,1),(1,26,0),(1,28,0)])
def test_modern_nginx_keeps_modern_configuration(version):
    runtime=load_script('migrate-panel.py');source='server { listen 443 ssl; http2 on; }'
    assert runtime.normalize_nginx_http2(source,version)==source


@pytest.mark.parametrize('source',[
    'http { http2 on; include sites-enabled/*; }',
    'server { http2 on; }',
    'server { listen 443 ssl http2; http2 off; }',
    'server { listen 443; http2 on; http2 off; }',
    'server { listen 443; http2 unexpected; }',
])
def test_ambiguous_layout_is_blocked_instead_of_silently_losing_http2(source):
    runtime=load_script('migrate-panel.py')
    with pytest.raises(RuntimeError) as error:runtime.normalize_nginx_http2(source,(1,24,0))
    assert error.value.code=='MIG_NGINX_COMPAT'


def test_http2_literals_and_comments_do_not_require_a_module():
    runtime=load_script('migrate-panel.py')
    assert not runtime.uses_http2('# http2 on;\nserver { add_header X-Example http2; return 200 "http2 on;"; }')
    assert runtime.uses_http2('server { listen 443 ssl http2; }')
    assert runtime.uses_http2('server { listen 443 ssl; http2 on; }')


def test_component_probe_adapts_in_memory_before_restore(monkeypatch):
    runtime=load_script('migrate-panel.py')
    monkeypatch.setattr(runtime,'command',lambda *a,**kw:SimpleNamespace(returncode=0,stdout='nginx/1.24.0 --with-http_v2_module',stderr=''))
    files={'etc/nginx/sites-available/md-next.conf':b'server { listen 443 ssl; http2 on; }'}
    adapted=runtime.component_capabilities(files)
    assert b'listen 443 ssl http2;' in adapted['etc/nginx/sites-available/md-next.conf']
    assert files['etc/nginx/sites-available/md-next.conf']==b'server { listen 443 ssl; http2 on; }'


@pytest.mark.parametrize('build,code',[
    ('nginx/1.24.0','MIG_NGINX_MODULE'),('unrecognized','MIG_NGINX_VERSION'),
])
def test_incompatible_nginx_has_specific_error(monkeypatch,build,code):
    runtime=load_script('migrate-panel.py')
    monkeypatch.setattr(runtime,'command',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=build,stderr=''))
    with pytest.raises(RuntimeError) as error:runtime.component_capabilities({'etc/nginx/sites-available/md-next.conf':b'server { listen 443 ssl; http2 on; }'})
    assert error.value.code==code


@pytest.mark.parametrize('binary,code',[('nginx','MIG_NGINX_CONFIG'),('/usr/local/bin/xray','MIG_XRAY_CONFIG')])
def test_native_configuration_validation_stays_mandatory(monkeypatch,binary,code):
    runtime=load_script('migrate-panel.py')
    monkeypatch.setattr(runtime,'command',lambda *args,**kw:SimpleNamespace(returncode=1 if args[0]==binary else 0))
    with pytest.raises(RuntimeError) as error:runtime.validate_components()
    assert error.value.code==code


def test_reboot_waits_for_changed_boot_and_keeps_the_new_connection(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');monkeypatch.setattr(worker,'STATE',tmp_path)
    closed=[]
    old=SimpleNamespace(close=lambda:closed.append('old'))
    new=SimpleNamespace(close=lambda:closed.append('new'))
    old_sock=SimpleNamespace(close=lambda:closed.append('old_sock'))
    new_sock=SimpleNamespace(close=lambda:closed.append('new_sock'))
    commands=[]
    def execute(transport,command,**kw):
        commands.append(command)
        if 'boot_id' in command:return 'old-boot' if transport is old else 'new-boot'
        if 'machine-id' in command:return 'same-target-machine'
        return ''
    monkeypatch.setattr(worker,'execute',execute)
    monkeypatch.setattr(worker,'connect_target',lambda *a,**kw:(new_sock,new))
    request={'host':'8.8.8.8','port':22,'action':'prepare'}
    assert worker.reboot_target(request,old_sock,old)==(new_sock,new)
    assert closed==['old','old_sock']
    assert any('systemctl reboot' in command for command in commands)
    state=json.loads((tmp_path/'job.json').read_text())
    assert [item['stage'] for item in state['history']]==['TARGET_REBOOT_START','TARGET_REBOOT_WAIT','TARGET_RECONNECTED']


def test_reboot_never_accepts_a_changed_ssh_key(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');monkeypatch.setattr(worker,'STATE',tmp_path)
    connection=SimpleNamespace(close=lambda:None)
    monkeypatch.setattr(worker,'execute',lambda *a,**kw:'before')
    def changed(*a,**kw):raise worker.events.MigrationError('MIG_SSH_KEY')
    monkeypatch.setattr(worker,'connect_target',changed)
    with pytest.raises(RuntimeError) as error:worker.reboot_target({'host':'8.8.8.8','port':22,'action':'prepare'},connection,connection)
    assert error.value.code=='MIG_SSH_KEY'


def test_reboot_has_bounded_wait(tmp_path,monkeypatch):
    worker=load_script('migration-ssh.py');monkeypatch.setattr(worker,'STATE',tmp_path)
    connection=SimpleNamespace(close=lambda:None);clock=[0]
    monkeypatch.setattr(worker,'execute',lambda *a,**kw:'before')
    monkeypatch.setattr(worker.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(worker.time,'sleep',lambda n:clock.__setitem__(0,301))
    def offline(*a,**kw):raise OSError('offline')
    monkeypatch.setattr(worker,'connect_target',offline)
    with pytest.raises(RuntimeError) as error:worker.reboot_target({'host':'8.8.8.8','port':22,'action':'prepare'},connection,connection)
    assert error.value.code=='MIG_REBOOT_TIMEOUT'
