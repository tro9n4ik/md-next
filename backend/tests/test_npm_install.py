"""Проверка резервных источников без загрузок и установки пакетов."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest


@pytest.fixture
def installer(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/npm-install.py'
    spec = importlib.util.spec_from_file_location('npm_install_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path/'package-lock.json').write_text(json.dumps({'packages': {
        '': {'name': 'test'},
        'node_modules/example': {'resolved': 'https://registry.npmjs.org/example/-/example-1.0.0.tgz',
                                 'integrity': 'sha512-example'},
    }}))
    monkeypatch.setattr(module, 'configured_registry', lambda *_: module.PRIMARY)
    return module, tmp_path


def test_success_does_not_contact_mirror(installer):
    module, directory = installer
    runner = Mock(return_value=(0, 'added 1 package', False))
    module.install(directory, env={}, runner=runner)
    runner.assert_called_once()
    assert '--registry=' + module.PRIMARY in runner.call_args.args[0]


@pytest.mark.parametrize('output,timeout', [
    ('npm error code ETIMEDOUT', False), ('npm error ECONNRESET', False),
    ('npm error EAI_AGAIN', False), ('npm error E503', False), ('', True),
])
def test_network_failure_uses_mirror_and_retains_lock_integrity(installer, output, timeout):
    module, directory = installer
    before = (directory/'package-lock.json').read_bytes()
    runner = Mock(side_effect=[(1, output, timeout), (0, 'added 1 package', False)])
    module.install(directory, env={'npm_config_cache': '/cache'}, runner=runner)
    assert runner.call_count == 2
    args = runner.call_args.args[0]
    assert args[:2] == ['npm', 'ci']
    assert '--registry=' + module.MIRRORS[0] in args
    assert '--strict-ssl=true' in args and '--replace-registry-host=npmjs' in args
    assert not any('trusted-host' in arg or 'force' in arg or 'install' == arg for arg in args)
    assert (directory/'package-lock.json').read_bytes() == before
    assert runner.call_args.args[2]['npm_config_cache'] == '/cache'


@pytest.mark.parametrize('output', [
    'npm error EINTEGRITY checksum failed', 'npm error EUSAGE lock mismatch',
    'npm error ERESOLVE', 'npm error ETARGET', 'npm error E401', 'npm error E403',
    'npm error E404', 'npm error ENOSPC', 'npm error EACCES',
    'npm error SELF_SIGNED_CERT_IN_CHAIN', 'npm error CERT_HAS_EXPIRED',
    'npm error command failed\nnode-gyp compilation failed',
])
def test_non_network_error_does_not_change_registry(installer, output):
    module, directory = installer
    runner = Mock(return_value=(1, output, False))
    with pytest.raises(RuntimeError, match='Источник не менялся'):
        module.install(directory, env={}, runner=runner)
    assert runner.call_count == 1


def test_integrity_failure_wins_over_network_hint(installer):
    module, _ = installer
    assert not module.network_failure('EINTEGRITY while retrying ETIMEDOUT', timed_out=True)


def test_all_network_failures_have_actionable_final_error(installer):
    module, directory = installer
    runner = Mock(return_value=(1, 'npm error ETIMEDOUT', False))
    with pytest.raises(RuntimeError, match='NPM_NETWORK'):
        module.install(directory, env={}, runner=runner)
    assert runner.call_count == 1 + len(module.MIRRORS)


def test_private_registry_is_never_replaced(installer, monkeypatch):
    module, directory = installer
    monkeypatch.setattr(module, 'configured_registry', lambda *_: 'https://private.example/')
    runner = Mock(return_value=(1, 'npm error ETIMEDOUT', False))
    with pytest.raises(RuntimeError):
        module.install(directory, env={}, runner=runner)
    assert runner.call_count == 1
    assert '--registry=https://private.example/' in runner.call_args.args[0]


@pytest.mark.parametrize('resolved,integrity', [
    ('https://private.example/example.tgz', 'sha512-value'),
    ('http://registry.npmjs.org/example.tgz', 'sha512-value'),
    ('https://registry.npmjs.org/example.tgz', ''),
    ('git+https://github.com/example/example', 'sha512-value'),
])
def test_non_public_or_unhashed_lock_is_not_redirected(installer, resolved, integrity):
    module, directory = installer
    (directory/'package-lock.json').write_text(json.dumps({'packages': {
        '': {}, 'node_modules/example': {'resolved': resolved, 'integrity': integrity}}}))
    runner = Mock(return_value=(1, 'npm error ETIMEDOUT', False))
    with pytest.raises(RuntimeError):
        module.install(directory, env={}, runner=runner)
    assert runner.call_count == 1


def test_missing_lock_fails_before_any_installation(installer):
    module, directory = installer
    (directory/'package-lock.json').unlink()
    runner = Mock()
    with pytest.raises(ValueError, match='package-lock'):
        module.install(directory, env={}, runner=runner)
    runner.assert_not_called()


def test_timeout_kills_process_and_reads_log(installer, monkeypatch):
    module, directory = installer
    process = Mock(pid=98765)
    process.wait.side_effect = [subprocess.TimeoutExpired('npm', 120), 0]
    def launch(*args, **kwargs):
        kwargs['stdout'].write(b'npm error ETIMEDOUT')
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    if os.name != 'nt':
        kill = Mock()
        monkeypatch.setattr(module.os, 'killpg', kill)
    code, output, timed_out = module.run_attempt(['npm', 'ci'], directory, {}, 120)
    assert timed_out and code == 124 and 'ETIMEDOUT' in output
    if os.name == 'nt':
        process.kill.assert_called_once()
    else:
        kill.assert_called_once()


def test_config_is_not_changed_and_credentials_are_redacted(installer):
    module, directory = installer
    config = directory/'.npmrc'
    config.write_text('strict-ssl=true\n')
    original = dict(os.environ)
    module.install(directory, runner=Mock(return_value=(0, '', False)))
    assert config.read_text() == 'strict-ssl=true\n'
    assert dict(os.environ) == original
    text = module.safe_output('https://user:password@registry.example/ _authToken=secret-value')
    assert 'user:password' not in text and 'secret-value' not in text


def test_installer_and_updater_use_same_trusted_helper():
    root = Path(__file__).resolve().parents[2]
    shell = (root/'scripts/install.sh').read_text(encoding='utf-8')
    assert shell.count('scripts/npm-install.py') == 2
    assert 'npm install --silent' not in shell
    assert "with_name('npm-install.py')" in (root/'scripts/update-panel.py').read_text(encoding='utf-8')
    assert "'npm-install.py': ROOT/'scripts/npm-install.py'" in (root/'scripts/install-privilege-separation.py').read_text(encoding='utf-8')


def test_real_project_lock_with_bundled_dependencies_allows_mirror(installer):
    module, _ = installer
    root = Path(__file__).resolve().parents[2]
    assert module.public_lock(root/'frontend')


def test_bundle_without_hashed_parent_does_not_allow_mirror(installer):
    module, directory = installer
    (directory/'package-lock.json').write_text(json.dumps({'packages': {
        '': {}, 'node_modules/invalid': {'inBundle': True, 'version': '1.0.0'}}}))
    assert not module.public_lock(directory)
