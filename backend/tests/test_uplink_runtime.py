"""Подготовка сохраняет действующий WARP и не выполняет команды из входных данных."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    source = Path(__file__).parents[2]/'scripts/warp-uplink-runtime.py'
    spec = importlib.util.spec_from_file_location('uplink_runtime_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = tmp_path/'xray.json'
    config.write_text(json.dumps({'outbounds': [{'tag': 'node-7', 'protocol': 'vless'}]}))
    monkeypatch.setattr(module, 'XRAY', config)
    monkeypatch.setattr(module, 'BACKUPS', tmp_path/'backups')
    monkeypatch.setattr(module, 'STATE', tmp_path/'state')
    module.STATE.mkdir()
    monkeypatch.setattr(module, 'REGISTRATION_DIR', tmp_path/'registration')
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    return module


@pytest.mark.parametrize('node_id,port', [(True, 40000), (7, True), ('7', 40000), (7, -1), (7, 65536), (8, 40000)])
def test_preparation_rejects_unknown_node_and_invalid_port(runtime, node_id, port):
    with pytest.raises(ValueError):
        runtime.validate(node_id, port)


def configure(runtime, monkeypatch, *, connected=True, mode='WarpProxy', port=40000, registered=True, active=True):
    monkeypatch.setattr(runtime.shutil, 'which', lambda *args, **kwargs: '/usr/bin/warp-cli')
    calls = []
    def cli(*args, **kwargs):
        calls.append(args)
        if args == ('settings',):
            return SimpleNamespace(returncode=0, stdout=f'(user set) Mode: {mode}\n(user set) Proxy port: {port}', stderr='')
        if args == ('status',):
            return SimpleNamespace(returncode=0, stdout='Status update: '+('Connected' if connected else 'Disconnected'), stderr='')
        if args == ('registration', 'show'):
            return SimpleNamespace(returncode=0 if registered else 1, stdout='Account type: Free' if registered else 'Missing registration', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(runtime, 'cli', cli)
    def command(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='active' if active and args[1] == 'is-active' else '', stderr='')
    commands = Mock(side_effect=command)
    monkeypatch.setattr(runtime, 'command', commands)
    monkeypatch.setattr(runtime, 'install_package', Mock())
    monkeypatch.setattr(runtime, 'trace', Mock())
    monkeypatch.setattr(runtime, 'update', Mock())
    return calls, commands


def test_existing_proxy_and_registration_are_reused(runtime, monkeypatch):
    calls, _ = configure(runtime, monkeypatch)
    runtime.prepare(7, 40000)
    assert not any(call[0] in {'mode', 'proxy', 'connect', 'disconnect'} for call in calls)
    assert all(call != ('registration', 'new') for call in calls)
    assert runtime.update.call_args.args[0] == 'ready'


@pytest.mark.parametrize('mode,port', [('WarpWithDnsOverHttps', 40000), ('WarpProxy', 41000)])
def test_connected_external_warp_is_not_reconfigured(runtime, monkeypatch, mode, port):
    calls, _ = configure(runtime, monkeypatch, mode=mode, port=port)
    with pytest.raises(ValueError, match='другой режим или порт'):
        runtime.prepare(7, 40000)
    assert not any(call[0] in {'mode', 'proxy', 'connect', 'disconnect'} for call in calls)


def test_missing_registration_uses_selected_node_and_bounded_cleanup_service(runtime, monkeypatch):
    calls, commands = configure(runtime, monkeypatch, connected=False, registered=False)
    runtime.prepare(7, 40000)
    registration = next(call.args[0] for call in commands.call_args_list if call.args[0][0] == '/usr/bin/systemd-run')
    assert '--unit=md-next-warp-registration' in registration
    assert '--property=RuntimeMaxSec=60' in registration
    assert any(item.startswith('--property=ExecStopPost=') and item.endswith(' --cleanup') for item in registration)
    assert registration[-4:] == ['--node-id', '7', '--config', str(runtime.XRAY)]
    assert ('mode', 'proxy') in calls and ('connect',) in calls
    assert ('registration', 'new') not in calls


def test_connection_failure_restores_mode_port_and_initially_stopped_service(runtime, monkeypatch):
    calls, commands = configure(runtime, monkeypatch, connected=False, mode='WarpWithDnsOverHttps', port=41000, active=False)
    runtime.trace.side_effect = RuntimeError('test failure')
    with pytest.raises(RuntimeError):
        runtime.prepare(7, 40000)
    assert ('disconnect',) in calls
    assert ('mode', 'warp+doh') in calls
    assert ('proxy', 'port', '41000') in calls
    assert any(call.args[0] == ['/usr/bin/systemctl', 'stop', 'warp-svc'] for call in commands.call_args_list)
    assert runtime.update.call_args.args[0] == 'failed'
    assert ('registration', 'delete') not in calls


def test_failed_trace_does_not_disconnect_previously_working_proxy(runtime, monkeypatch):
    calls, _ = configure(runtime, monkeypatch)
    runtime.trace.side_effect = RuntimeError('test failure')
    with pytest.raises(RuntimeError):
        runtime.prepare(7, 40000)
    assert not any(call[0] in {'mode', 'proxy', 'connect', 'disconnect'} for call in calls)


def test_package_failure_restores_apt_files(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, 'which', lambda *args, **kwargs: None)
    release = tmp_path/'os-release'
    release.write_text('ID=debian\nVERSION_CODENAME=bookworm\n')
    monkeypatch.setattr(runtime, 'OS_RELEASE', release)
    key, source = tmp_path/'key.gpg', tmp_path/'cloudflare.list'
    key.write_bytes(b'original public key')
    source.write_bytes(b'original apt source')
    monkeypatch.setattr(runtime, 'KEY', key)
    monkeypatch.setattr(runtime, 'SOURCE', source)
    monkeypatch.setattr(runtime, 'update', Mock())
    monkeypatch.setattr(runtime, 'command', Mock())
    monkeypatch.setattr(runtime.urllib.request, 'urlopen', Mock(side_effect=OSError('test network failure')))
    backup = tmp_path/'backup'
    backup.mkdir()
    with pytest.raises(OSError):
        runtime.install_package(backup)
    assert key.read_bytes() == b'original public key'
    assert source.read_bytes() == b'original apt source'
    assert (backup/key.name).read_bytes() == b'original public key'


def test_unsupported_distribution_never_changes_package_repositories(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, 'which', lambda *args, **kwargs: None)
    release = tmp_path/'os-release'
    release.write_text('ID=other\nVERSION_CODENAME=unknown\n')
    monkeypatch.setattr(runtime, 'OS_RELEASE', release)
    command = Mock()
    monkeypatch.setattr(runtime, 'command', command)
    with pytest.raises(ValueError, match='Автоматическая установка'):
        runtime.install_package(tmp_path)
    command.assert_not_called()


def test_interrupted_job_cannot_remain_preparing_forever(runtime, monkeypatch):
    monkeypatch.setattr(runtime, 'read_status', lambda: {'phase': 'connecting'})
    update = Mock()
    monkeypatch.setattr(runtime, 'update', update)
    runtime.finish()
    assert update.call_args.args[0] == 'failed'
    monkeypatch.setattr(runtime, 'read_status', lambda: {'phase': 'ready'})
    update.reset_mock()
    runtime.finish()
    update.assert_not_called()


@pytest.mark.parametrize('mode,expected', [('WarpOnly', 'warp'), ('DnsOverHttps', 'doh'), ('WarpWithDnsOverHttps', 'warp+doh')])
def test_mode_restore_keeps_the_original_dns_behavior(runtime, mode, expected):
    assert runtime.parse_settings('Mode: '+mode)['mode'] == expected


def test_current_cli_reports_the_proxy_port_in_its_mode_line(runtime):
    assert runtime.parse_settings('(user set)\tMode: WarpProxy on port 40000') == {'mode': 'proxy', 'port': 40000}


def test_unknown_mode_is_not_changed(runtime, monkeypatch):
    calls, _ = configure(runtime, monkeypatch, connected=False, mode='UnknownFutureMode')
    with pytest.raises(ValueError, match='определить текущий режим'):
        runtime.prepare(7, 40000)
    assert not any(call[0] in {'mode', 'proxy', 'connect', 'disconnect'} for call in calls)


def test_service_timeout_restores_persisted_local_state(runtime, monkeypatch):
    calls, commands = configure(runtime, monkeypatch, active=False)
    (runtime.STATE/'recovery.json').write_text(json.dumps({'active': False, 'configured': True,
        'previous': {'mode': 'warp', 'port': 41000, 'connected': False}}))
    monkeypatch.setattr(runtime, 'read_status', lambda: {'phase': 'connecting'})
    runtime.finish()
    assert ('disconnect',) in calls
    assert ('mode', 'warp') in calls
    assert ('proxy', 'port', '41000') in calls
    assert any(call.args[0] == ['/usr/bin/systemctl', 'stop', 'warp-svc'] for call in commands.call_args_list)
    assert not (runtime.STATE/'recovery.json').exists()


def test_reboot_does_not_leave_a_stale_preparing_state(runtime, monkeypatch):
    (runtime.STATE/'status.json').write_text(json.dumps({'phase': 'installing', 'message': 'Test'}))
    monkeypatch.setattr(runtime, 'command', Mock(return_value=SimpleNamespace(stdout='inactive', returncode=3)))
    assert runtime.status()['phase'] == 'failed'
    assert runtime.read_status()['phase'] == 'installing'
