import importlib.util
import json
from pathlib import Path
import pytest

pytest.importorskip('pwd')
spec = importlib.util.spec_from_file_location('privileged_helper', Path(__file__).parents[2]/'scripts/privileged-helper.py')
helper = importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


@pytest.mark.parametrize('case', [
    {'operation': 'command', 'args': ['bash', '-c', 'id']},
    {'operation': 'command', 'args': ['python3', '/tmp/evil.py', '--config', '/etc/shadow']},
    {'operation': 'command', 'args': ['systemctl', 'restart', 'ssh']},
    {'operation': 'command', 'args': ['systemctl', 'stop', 'nginx']},
    {'operation': 'command', 'args': ['awg-quick', 'up', '/tmp/injected.conf']},
    {'operation': 'command', 'args': ['xray', 'run', '-test', '-format', 'json', '-config', '/etc/shadow']},
    {'operation': 'nginx', 'kind': 'path', 'value': '/;include /tmp/evil;'},
    {'operation': 'nginx', 'kind': 'sni', 'value': 'example.com; root /;'},
    {'operation': 'update-start', 'commit': '--option'},
])
def test_caller_cannot_choose_root_executable_paths_or_unrelated_services(case, monkeypatch):
    def forbidden(*args): raise AssertionError('A system command was reached')
    monkeypatch.setattr(helper, 'execute', forbidden)
    with pytest.raises(ValueError): helper.dispatch(case)


@pytest.mark.parametrize('line', ['PostUp = touch /root/escaped', 'PreDown = id', 'SaveConfig = true', 'DNS = 1.1.1.1'])
def test_awg_shell_hooks_not_accepted(line):
    with pytest.raises(ValueError): helper.validate_awg(('[Interface]\n'+line).encode())


def test_xray_file_and_device_settings_are_limited(tmp_path, monkeypatch):
    config = {'log': {'loglevel': 'warning'}, 'inbounds': [], 'outbounds': []}
    path = tmp_path/'xray.json';path.write_text(json.dumps(config));monkeypatch.setattr(helper, 'XRAY', path)
    assert helper.validate_xray(json.dumps(config)) == config
    config['inbounds'] = [{'protocol': 'tun', 'settings': {'name': 'foreign'}}]
    with pytest.raises(ValueError): helper.validate_xray(json.dumps(config))
    config['inbounds'] = [{'protocol': 'vless', 'streamSettings': {'tlsSettings': {'certificates': [{'keyFile': '/etc/shadow'}]}}}]
    with pytest.raises(ValueError): helper.validate_xray(json.dumps(config))
    config['inbounds'] = [];config['routing'] = {'rules': [{'domain': ['ext:/etc/shadow:tag']}]}
    with pytest.raises(ValueError): helper.validate_xray(json.dumps(config))


def test_installer_removes_archive_write_permissions_without_touching_public_data(tmp_path, monkeypatch):
    installer_spec = importlib.util.spec_from_file_location('privilege_installer', Path(__file__).parents[2]/'scripts/install-privilege-separation.py')
    installer = importlib.util.module_from_spec(installer_spec);installer_spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, 'ROOT', tmp_path)
    monkeypatch.setattr(installer.os, 'chown', lambda *args: None)
    code = tmp_path/'backend/app/main.py';code.parent.mkdir(parents=True);code.write_text('app code');code.chmod(0o666)
    public = tmp_path/'backend/app/static/fake';public.mkdir(parents=True);public.chmod(0o755)
    (public/'index.html').write_text('public data');(public/'index.html').chmod(0o644)
    installer.protect_code()
    assert code.stat().st_mode & 0o022 == 0
    assert (public/'index.html').stat().st_mode & 0o777 == 0o644


def test_helper_copies_detect_stale_content_and_unsafe_permissions(tmp_path, monkeypatch):
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('copy_checker', Path(__file__).parents[2]/'scripts/install-privilege-separation.py')
    installer = importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    source = tmp_path/'source.py';source.write_text('release code')
    lib = tmp_path/'lib';lib.mkdir();target = lib/'helper.py';target.write_text('release code');target.chmod(0o644)
    monkeypatch.setattr(installer, 'LIB', lib)
    monkeypatch.setattr(installer, 'helper_sources', lambda: {'helper.py': source})
    original_stat = Path.stat
    def root_stat(path, *args, **kwargs):
        stat = original_stat(path, *args, **kwargs)
        if path == target: return SimpleNamespace(st_uid=0, st_mode=stat.st_mode)
        return stat
    monkeypatch.setattr(Path, 'stat', root_stat)
    installer.verify_copies()
    target.write_text('old code')
    with pytest.raises(RuntimeError, match='differs'): installer.verify_copies()
    target.write_text('release code');target.chmod(0o666)
    with pytest.raises(RuntimeError, match='permissions'): installer.verify_copies()


def test_helper_isolated_python_ignores_pythonpath(tmp_path):
    import os
    import subprocess
    script = Path(__file__).parents[2]/'scripts/privileged-helper.py'
    assert script.read_text().splitlines()[0] == '#!/usr/bin/python3 -I'
    (tmp_path/'json.py').write_text("raise RuntimeError('untrusted import')")
    env = {**os.environ, 'PYTHONPATH': str(tmp_path)}
    result = subprocess.run(['/usr/bin/python3', '-I', str(script)], input='{}', text=True, capture_output=True, env=env, cwd=tmp_path)
    assert result.returncode == 1
    assert 'untrusted import' not in result.stderr
    assert json.loads(result.stdout)[0] == 1
