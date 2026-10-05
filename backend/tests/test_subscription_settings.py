import base64
from urllib.parse import unquote, urlsplit
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.models.client import Client, ClientProfile
from conftest import TestingSessionLocal

@pytest.mark.asyncio
async def test_subscription_name_persistence_and_owner(auth_headers):
    async with TestingSessionLocal() as db:
        c = Client(name='Владелец', phone='', email='', sub_token='name-test')
        db.add(c); await db.flush()
        db.add(ClientProfile(client_id=c.id, kind='vless_reality_tcp', uuid='stable-uuid', is_enabled=True))
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as api:
        assert (await api.get('/api/v1/settings/subscription', headers=auth_headers)).json()['name'] == 'MD-NEXT'
        old = await api.get('/api/v1/sub/name-test')
        assert (await api.put('/api/v1/settings/subscription', json={'name':'Другой'})).status_code in (401,403)
        for invalid in ['', ' '*5, 'x'*26, 'bad\nheader']:
            assert (await api.put('/api/v1/settings/subscription', headers=auth_headers, json={'name':invalid})).status_code == 422
        result = await api.put('/api/v1/settings/subscription', headers=auth_headers, json={'name':' Моя подписка '})
        assert result.status_code == 200
        assert (await api.get('/api/v1/settings/subscription', headers=auth_headers)).json()['name'] == 'Моя подписка'
        new = await api.get('/api/v1/sub/name-test')
        assert base64.b64decode(new.headers['profile-title'][7:]).decode() == 'Моя подписка'
        assert new.text == old.text
        assert unquote(urlsplit(base64.b64decode(new.text).decode()).fragment) == 'Владелец'
