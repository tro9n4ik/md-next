"""Отдельная квота CDN, общий учёт и автоматическое восстановление периода."""
import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from app.services.client_limits import access_allowed, add_months, cdn_quota_exhausted
from app.services.client_service import ClientService
from app.services import traffic_collector
from conftest import TestingSessionLocal


async def seed(limit=100, used=100):
    now = datetime.now(timezone.utc)
    async with TestingSessionLocal() as db:
        client = Client(name='Обход БС', phone='', email='', created_at=now, traffic_period_start=now,
                        cdn_monthly_traffic_limit=limit, cdn_monthly_traffic_down=used, sub_token='cdn-quota')
        db.add(client)
        await db.flush()
        cid = client.id
        db.add(ClientProfile(client_id=cid, kind='vless_xhttp_tls', uuid='cdn-quota-uuid', is_enabled=True))
        db.add_all([Setting(key='cdn.enabled', value='true'), Setting(key='cdn.domain', value='cdn.example.com'),
                    Setting(key='profiles.enabled.vless_xhttp_tls', value='true')])
        await db.commit()
    return cid


@pytest.mark.asyncio
async def test_exhausted_cdn_removed_from_server_and_subscription_only(auth_headers):
    cid = await seed()
    async with TestingSessionLocal() as db:
        client = await db.get(Client, cid)
        assert access_allowed(client) and cdn_quota_exhausted(client)
        with patch('app.services.client_service.apply_reality_sni', new=AsyncMock()), \
             patch('app.services.xray.XrayService.apply_config', return_value=(True, 'Готово')) as apply:
            assert (await ClientService.sync_xray_clients(db))[0]
            assert apply.call_args.kwargs['profile_options']['cdn_clients'] == []
            assert apply.call_args.kwargs['profile_options']['vless_xhttp_tls_clients'][0]['id'] == 'cdn-quota-uuid'
    with patch('app.api.clients._sync_protocols', new=AsyncMock()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
            response = await api.get('/api/v1/sub/cdn-quota')
            links = base64.b64decode(response.text).decode()
            assert '/cdn-get' not in links and links.count('vless://') == 1
            profiles = (await api.get(f'/api/v1/clients/{cid}/profiles', headers=auth_headers)).json()
            assert next(x for x in profiles['access'] if x['kind'] == 'cdn')['is_enabled']
            assert profiles['client']['cdn_quota_exhausted']
            assert (await api.put(f'/api/v1/clients/{cid}', headers=auth_headers, json={'cdn_monthly_traffic_limit':-1})).status_code == 422
            restored = await api.put(f'/api/v1/clients/{cid}', headers=auth_headers, json={'cdn_monthly_traffic_limit':200})
            assert restored.status_code == 200 and not restored.json()['cdn_quota_exhausted']
            assert restored.json()['cdn_monthly_traffic_used'] == 100
            assert base64.b64decode((await api.get('/api/v1/sub/cdn-quota')).text).decode().count('vless://') == 2


@pytest.mark.asyncio
async def test_cdn_email_separate_and_totals_not_double_counted(monkeypatch):
    cid = await seed(100, 0)
    async with TestingSessionLocal() as db:
        with patch('app.services.client_service.apply_reality_sni', new=AsyncMock()), \
             patch('app.services.xray.XrayService.apply_config', return_value=(True, 'Готово')) as apply:
            await ClientService.sync_xray_clients(db)
            options = apply.call_args.kwargs['profile_options']
            assert options['cdn_clients'][0]['email'] == f'c{cid}-cdn@md-next'
            assert options['vless_xhttp_tls_clients'][0]['email'] == f'c{cid}-vless_xhttp_tls@md-next'
    async def command(*args):
        if args[0] != 'xray': return ''
        return json.dumps({'stat':[
            {'name':f'user>>>c{cid}-cdn@md-next>>>traffic>>>uplink','value':40},
            {'name':f'user>>>c{cid}-cdn@md-next>>>traffic>>>downlink','value':60},
            {'name':f'user>>>c{cid}-vless_xhttp_tls@md-next>>>traffic>>>downlink','value':30}]})
    monkeypatch.setattr(traffic_collector, 'LIMITS_SYNC_PENDING', False)
    with patch.object(traffic_collector, '_run_command', side_effect=command), \
         patch.object(ClientService, 'sync_xray_clients', return_value=(False, 'Не применено')) as sync, \
         patch.object(traffic_collector.AWGService, 'sync_server_config', return_value=(True, 'Готово')):
        await traffic_collector._collect_client_traffic()
        assert traffic_collector.LIMITS_SYNC_PENDING
        async with TestingSessionLocal() as db:
            c = await db.get(Client, cid)
            assert c.cdn_monthly_traffic_up + c.cdn_monthly_traffic_down == 100
            assert c.monthly_traffic_up + c.monthly_traffic_down == c.traffic_total == 130
            assert c.is_active and not c.access_blocked and not c.cdn_access_blocked
            p = (await db.execute(select(ClientProfile))).scalar_one()
            assert p.traffic_up + p.traffic_down == 130
        sync.return_value = (True, 'Готово')
        with patch.object(traffic_collector, '_run_command', return_value='{}'):
            await traffic_collector._collect_client_traffic()
        assert not traffic_collector.LIMITS_SYNC_PENDING
        async with TestingSessionLocal() as db:
            c = await db.get(Client, cid)
            assert c.cdn_access_blocked and not c.access_blocked
            c.created_at = add_months(c.created_at, -1)
            c.traffic_period_start = c.created_at
            await db.commit()
        with patch.object(traffic_collector, '_run_command', return_value='{}'):
            await traffic_collector._collect_client_traffic()
        async with TestingSessionLocal() as db:
            c = await db.get(Client, cid)
            assert c.is_active and not c.cdn_access_blocked
            assert c.cdn_monthly_traffic_up + c.cdn_monthly_traffic_down == 0
            assert c.cdn_traffic_up + c.cdn_traffic_down == 100
            assert c.traffic_total == 130


@pytest.mark.asyncio
async def test_create_with_separate_limit_and_remove_limit(auth_headers):
    with patch('app.api.clients._sync_protocols', new=AsyncMock()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
            r = await api.post('/api/v1/clients', headers=auth_headers, json={'name':'Квота CDN','cdn_monthly_traffic_limit':1024})
            assert r.status_code == 201
            c = r.json()['client']
            assert c['cdn_monthly_traffic_limit'] == 1024 and c['monthly_traffic_limit'] == 0
            r = await api.put(f"/api/v1/clients/{c['id']}", headers=auth_headers, json={'cdn_monthly_traffic_limit':0})
            assert r.json()['cdn_monthly_traffic_limit'] == 0 and not r.json()['cdn_quota_exhausted']
