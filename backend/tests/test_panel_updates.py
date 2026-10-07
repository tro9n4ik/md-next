import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest

from app.main import app
from app.services import panel_updates as updates

SHA = 'a' * 40


@pytest.mark.skipif(os.name == 'nt', reason='Linux updater uses flock and runtime symlinks')
def test_activation_preserves_uploaded_placeholder(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('placeholder_updater_test', Path(__file__).resolve().parents[2] / 'scripts/update-panel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'active_awg_units', lambda: [])
    root, repository, state = tmp_path / 'installed', tmp_path / 'repository', tmp_path / 'state'
    for base in (root, repository):
        (base / 'backend/app/static/fake').mkdir(parents=True)
        (base / 'frontend/dist/assets').mkdir(parents=True)
        (base / 'frontend/dist/index.html').write_text('frontend')
    (root / 'backend/app/static/fake/index.html').write_text('Uploaded page')
    (root / 'backend/app/static/fake/own.txt').write_text('Own asset')
    (repository / 'backend/app/static/fake/index.html').write_text('Bundled page')
    (repository / 'backend/app/static/fake/welcome.txt').write_text('Bundled asset')
    (repository / 'backend/new.py').write_text('new version')
    (root / 'backend/venv').mkdir()
    (root / 'backend/.env').write_text('')
    (root / 'venv-releases').mkdir()
    state.mkdir()
    updater = module.Updater(root, state, {'commit': SHA})
    updater.new_venv = tmp_path / 'new-runtime'
    updater.new_venv.mkdir()
    updater.backup = tmp_path / 'backup'
    updater.backup.mkdir()
    monkeypatch.setattr(module, 'snapshot_database', lambda *args: None)
    monkeypatch.setattr(module, 'identities', lambda *args: [(1, 'unchanged')])
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args: b'backend/app/static/fake/index.html\0backend/app/static/fake/welcome.txt\0backend/new.py\0')
    monkeypatch.setattr(updater, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(updater, 'wait_ready', lambda: None)
    updater.activate(repository)
    assert (root / 'backend/app/static/fake/index.html').read_text() == 'Uploaded page'
    assert (root / 'backend/app/static/fake/own.txt').read_text() == 'Own asset'
    assert not (root / 'backend/app/static/fake/welcome.txt').exists()
    assert (root / 'backend/new.py').read_text() == 'new version'

@pytest.fixture(autouse=True)
def update_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, 'STATE_DIR', tmp_path / 'updates')

@pytest.mark.asyncio
async def test_update_endpoints_require_auth():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/v1/system/updates')).status_code == 401
        assert (await client.post('/api/v1/system/updates/check', json={})).status_code == 401
        assert (await client.post('/api/v1/system/updates/install', json={'commit': SHA, 'confirm': True})).status_code == 401

@pytest.mark.asyncio
async def test_update_rejects_unconfirmed_or_shell_input(auth_headers, monkeypatch):
    start = AsyncMock()
    monkeypatch.setattr('app.api.updates.start_update', start)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/v1/system/updates/install', headers=auth_headers, json={'commit': SHA})).status_code == 400
        assert (await client.post('/api/v1/system/updates/install', headers=auth_headers, json={'commit': 'main;touch /tmp/injected', 'confirm': True})).status_code == 422
    start.assert_not_awaited()

@pytest.mark.asyncio
async def test_check_requires_both_workflows_and_detects_installed_prefix(monkeypatch):
    factory = httpx.AsyncClient
    def respond(request):
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': SHA, 'commit': {'message': 'New release', 'committer': {'date': '2026-10-07T00:00:00Z'}}})
        return httpx.Response(200, json={'workflow_runs': [{'path': '.github/workflows/ci.yml', 'conclusion': 'success'}]})
    monkeypatch.setattr(updates.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(updates, 'installed_info', lambda: {'version': '2.4.6', 'commit': SHA[:7]})
    result = await updates.check_update('synthetic-test-token')
    assert result['available'] is False
    assert result['ready'] is False
    assert 'synthetic-test-token' not in json.dumps(result)

@pytest.mark.asyncio
async def test_changed_commit_cannot_be_installed(monkeypatch):
    monkeypatch.setattr(updates, 'get_status', AsyncMock(return_value={'can_install': True, 'job': {'phase': 'idle'}}))
    monkeypatch.setattr(updates, 'check_update', AsyncMock(return_value={'commit': 'b' * 40, 'ready': True, 'available': True}))
    run = AsyncMock()
    monkeypatch.setattr(updates, 'run_cmd', run)
    with pytest.raises(ValueError, match='Версия изменилась'):
        await updates.start_update(SHA)
    run.assert_not_awaited()

@pytest.mark.asyncio
async def test_dispatch_failure_removes_secret_and_preserves_error_status(monkeypatch):
    monkeypatch.setattr(updates, 'get_status', AsyncMock(return_value={'can_install': True, 'job': {'phase': 'idle'}}))
    monkeypatch.setattr(updates, 'check_update', AsyncMock(return_value={'commit': SHA, 'ready': True, 'available': True}))
    monkeypatch.setattr(updates, 'run_cmd', AsyncMock(return_value=(1, '', 'failure')))
    with pytest.raises(RuntimeError):
        await updates.start_update(SHA, 'synthetic-private-token')
    assert not list(updates.STATE_DIR.glob('request-*.json'))
    assert updates.read_state()['phase'] == 'error'
    assert 'synthetic-private-token' not in (updates.STATE_DIR / 'status.json').read_text()

@pytest.mark.asyncio
async def test_dispatch_keeps_credentials_out_of_command_and_status(monkeypatch):
    monkeypatch.setattr(updates, 'get_status', AsyncMock(return_value={'can_install': True, 'job': {'phase': 'idle'}}))
    monkeypatch.setattr(updates, 'check_update', AsyncMock(return_value={'commit': SHA, 'ready': True, 'available': True}))
    run = AsyncMock(return_value=(0, '', ''))
    monkeypatch.setattr(updates, 'run_cmd', run)
    result = await updates.start_update(SHA, 'synthetic-private-token')
    request = next(updates.STATE_DIR.glob('request-*.json'))
    assert json.loads(request.read_text())['token'] == 'synthetic-private-token'
    assert 'synthetic-private-token' not in str(run.call_args)
    assert 'synthetic-private-token' not in json.dumps(result)
    if os.name != 'nt':
        assert request.stat().st_mode & 0o777 == 0o600

@pytest.mark.skipif(os.name == 'nt', reason='Linux updater uses flock and symlinks')
def test_updater_restores_files_database_and_runtime_after_failed_activation(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('panel_updater_test', Path(__file__).resolve().parents[2] / 'scripts/update-panel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'active_awg_units', lambda: [])
    root = tmp_path / 'md-next'
    backend = root / 'backend'
    backend.mkdir(parents=True)
    (backend / '.env').write_text('JWT_SECRET_KEY=synthetic-local-test\n')
    (backend / 'application.py').write_text('previous version')
    old_runtime = root / 'venv-releases/previous'
    old_runtime.mkdir(parents=True)
    (backend / 'venv').symlink_to(old_runtime)
    with sqlite3.connect(backend / 'md_next.db') as db:
        db.execute('CREATE TABLE clients (id INTEGER, uuid TEXT)')
        db.execute("INSERT INTO clients VALUES (1, 'original-client')")
    config = tmp_path / 'xray.json'
    config.write_text('previous configuration')
    monkeypatch.setattr(module, 'CONFIGS', [config])
    state = tmp_path / 'state'
    state.mkdir()
    updater = module.Updater(root, state, {'commit': SHA, 'python': '/usr/bin/python3'})
    monkeypatch.setattr(updater, 'prepare', lambda directory: directory)
    monkeypatch.setattr(updater, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(updater, 'wait_ready', lambda: None)
    def fail_activation(repository):
        updater.activated = True
        updater.old_venv = old_runtime
        (backend / 'application.py').write_text('broken version')
        config.write_text('broken configuration')
        with sqlite3.connect(backend / 'md_next.db') as db:
            db.execute("UPDATE clients SET uuid='changed-client'")
        raise RuntimeError('Migration/startup failed')
    monkeypatch.setattr(updater, 'activate', fail_activation)
    assert updater.execute() is False
    assert (backend / 'application.py').read_text() == 'previous version'
    assert config.read_text() == 'previous configuration'
    assert module.identities(root) == [(1, 'original-client')]
    assert (backend / 'venv').resolve() == old_runtime
    assert json.loads((state / 'status.json').read_text())['phase'] == 'rolled_back'

@pytest.mark.skipif(os.name == 'nt', reason='Linux updater uses flock')
def test_failed_preparation_does_not_stop_service_or_change_application(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('panel_updater_prepare', Path(__file__).resolve().parents[2] / 'scripts/update-panel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / 'state'
    state.mkdir()
    updater = module.Updater(tmp_path / 'application', state, {'commit': SHA})
    calls = []
    monkeypatch.setattr(updater, 'run', lambda args, **kwargs: calls.append(args))
    def fail(directory):
        raise RuntimeError('Dependency resolution failed')
    monkeypatch.setattr(updater, 'prepare', fail)
    assert updater.execute() is False
    assert not calls
    assert not (tmp_path / 'application').exists()
    assert json.loads((state / 'status.json').read_text())['phase'] == 'error'


@pytest.mark.skipif(os.name == 'nt', reason='Linux updater uses flock')
def test_rollback_selects_only_previously_running_awg_units(monkeypatch):
    spec = importlib.util.spec_from_file_location('panel_updater_units', Path(__file__).resolve().parents[2] / 'scripts/update-panel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from subprocess import CompletedProcess
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kwargs: CompletedProcess(args, 0 if args[-1] == 'awg-quick@awg0.service' else 3, 'active\n' if args[-1] == 'awg-quick@awg0.service' else 'inactive\n'))
    assert module.active_awg_units() == ['awg-quick@awg0.service']
