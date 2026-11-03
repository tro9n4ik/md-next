from unittest.mock import AsyncMock, patch
from pathlib import Path
import os

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.migration import address_kind
from conftest import TestingSessionLocal
from test_subadmins import account


@pytest.mark.parametrize('value,kind,host', [
    ('https://panel.example.com/sub/private-token', 'domain', 'panel.example.com'),
    ('VPN.EXAMPLE.COM.', 'domain', 'vpn.example.com'),
    ('8.8.8.8', 'ip', '8.8.8.8'),
    ('127.0.0.1', 'local', '127.0.0.1'),
    ('[::1]:443', 'local', '::1'),
    ('2606:4700:4700::1111', 'ip', '2606:4700:4700::1111'),
    ('localhost', 'local', 'localhost'),
    ('', 'missing', ''),
    ('bad host', 'invalid', ''),
    ('https://user:secret@example.com', 'invalid', ''),
])
def test_addresses(value, kind, host):
    assert address_kind(value) == (kind, host)


@pytest.mark.asyncio
async def test_source_report_is_admin_only_and_never_contains_secrets(auth_headers, monkeypatch):
    monkeypatch.setenv('PANEL_PUBLIC_URL', 'https://panel.example.com')
    jwt_secret = os.environ['JWT_SECRET_KEY']
    async with TestingSessionLocal() as db:
        client = Client(name='Private name', email='private@example.com', phone='123', sub_token='private-subscription-token')
        db.add(client); await db.flush()
        db.add(ClientProfile(client_id=client.id, kind='mtproto', auth='private-profile-auth', is_enabled=True))
        db.add(Setting(key='telegram.bot_token', value='private-bot-token'))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        assert (await api.get('/api/v1/migration/source')).status_code == 401
        _, limited = await account(auth_headers, name='migration-viewer')
        assert (await api.get('/api/v1/migration/source', headers=limited)).status_code == 403
        with patch('app.services.migration.get_profile_settings', new=AsyncMock(return_value={'protocol.reality.server_address':'8.8.8.8'})):
            response = await api.get('/api/v1/migration/source', headers=auth_headers)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()
        assert data['can_switch'] is False
        assert data['phase'] == 'source_preflight'
        assert data['counts']['clients'] == 1
        assert data['counts']['profiles'] == 1
        assert data['source_checks_passed'] is False
        assert next(row for row in data['addresses'] if row['key']=='profiles')['status'] == 'warning'
        assert next(row for row in data['checks'] if row['key']=='target')['status'] == 'pending'
        for secret in ['private-subscription-token','private-profile-auth','private-bot-token','Private name','private@example.com',jwt_secret]:
            assert secret not in response.text
    async with TestingSessionLocal() as db:
        assert (await db.get(Client, client.id)).sub_token == 'private-subscription-token'


@pytest.mark.asyncio
async def test_jobs_need_confirmation_and_do_not_echo_credentials(auth_headers):
    payload={'host':'8.8.8.8','port':22,'action':'prepare','fingerprint':'SHA256:'+'a'*43,
             'ssh_password':'synthetic-ssh-password','password':'synthetic-package-password','email':'demo@example.com','confirm':False}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as api:
        assert (await api.post('/api/v1/migration/jobs',json=payload)).status_code==401
        with patch('app.api.migration.enabled',return_value=True), patch('app.api.migration.call',new=AsyncMock(return_value=(0,'{"phase":"connecting","message":"Запущено"}',''))) as call:
            assert (await api.post('/api/v1/migration/jobs',headers=auth_headers,json=payload)).status_code==400
            call.assert_not_awaited()
            payload['confirm']=True
            response=await api.post('/api/v1/migration/jobs',headers=auth_headers,json=payload)
            assert response.status_code==202
            assert 'synthetic-ssh-password' not in response.text and 'synthetic-package-password' not in response.text
            assert call.call_args.kwargs['request']['ssh_password']=='synthetic-ssh-password'
            payload['action']='activate'
            assert (await api.post('/api/v1/migration/jobs',headers=auth_headers,json=payload)).status_code==400


@pytest.mark.asyncio
async def test_staging_blocks_writes_but_keeps_status(auth_headers,monkeypatch):
    monkeypatch.setenv('MDNEXT_MIGRATION_STAGING','1')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as api:
        response=await api.post('/api/v1/clients',headers=auth_headers,json={'name':'Do not create'})
        assert response.status_code==503
        with patch('app.api.migration.enabled',return_value=False):
            assert (await api.get('/api/v1/migration/jobs',headers=auth_headers)).status_code==200


@pytest.mark.asyncio
async def test_package_download_and_cleanup_are_scoped(auth_headers,monkeypatch,tmp_path):
    from app.api import migration
    monkeypatch.setattr(migration,'BUNDLE_DIR',tmp_path)
    name='a'*32+'.mdmove';(tmp_path/name).write_bytes(b'encrypted-test-data')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as api:
        assert (await api.get('/api/v1/migration/packages/'+name)).status_code==401
        response=await api.get('/api/v1/migration/packages/'+name,headers=auth_headers)
        assert response.content==b'encrypted-test-data' and response.headers['cache-control']=='no-store'
        assert (await api.get('/api/v1/migration/packages/not-a-package',headers=auth_headers)).status_code==404
        assert (await api.delete('/api/v1/migration/packages/'+name,headers=auth_headers)).status_code==204
        assert not (tmp_path/name).exists()


@pytest.mark.asyncio
async def test_staging_lifespan_never_starts_background_services(monkeypatch):
    from app.main import lifespan
    monkeypatch.setenv('MDNEXT_MIGRATION_STAGING','1')
    from types import SimpleNamespace
    with patch('app.main.watchdog.start') as watchdog, patch('app.main.bot_manager.start',new=AsyncMock()) as bot, patch('app.main.engine',new=SimpleNamespace(dispose=AsyncMock())):
        async with lifespan(app):
            watchdog.assert_not_called();bot.assert_not_awaited()


@pytest.mark.asyncio
async def test_validation_never_echoes_failed_secret_input(auth_headers):
    payload={'host':'8.8.8.8','action':'prepare','fingerprint':'SHA256:'+'a'*43,
             'ssh_password':'ssh-secret-value','password':'tiny-key','confirm':True}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as api:
        response=await api.post('/api/v1/migration/jobs',headers=auth_headers,json=payload)
        assert response.status_code==422
        assert response.json()['detail']['code']=='MIG_INPUT'
        assert 'tiny-key' not in response.text and 'ssh-secret-value' not in response.text


@pytest.mark.asyncio
async def test_structured_worker_error_reaches_ui_without_credentials(auth_headers):
    payload={'host':'8.8.8.8','action':'prepare','fingerprint':'SHA256:'+'a'*43,
             'ssh_password':'ssh-secret-value','password':'package-secret-value','confirm':True}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as api:
        with patch('app.api.migration.enabled',return_value=True),patch('app.api.migration.call',new=AsyncMock(return_value=(1,'{"code":"MIG_BUSY","message":"Операция уже выполняется"}',''))):
            response=await api.post('/api/v1/migration/jobs',headers=auth_headers,json=payload)
            assert response.status_code==409 and response.json()['detail']['code']=='MIG_BUSY'
            assert 'secret-value' not in response.text
