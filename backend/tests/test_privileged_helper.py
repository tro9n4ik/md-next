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
