from unittest.mock import AsyncMock, patch

import pytest

from app.api.system import get_system_health
from app.models.client import Client, ClientProfile
from app.models.setting import Setting
from conftest import TestingSessionLocal


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,running,available,expected', [
    (False, False, False, 'disabled'),
    (True, True, True, 'ok'),
    (True, False, True, 'warning'),
    (True, False, False, 'disabled'),
])
async def test_mtproto_health(enabled, running, available, expected):
    async with TestingSessionLocal() as db:
        db.add(Setting(key='profiles.enabled.mtproto', value=str(enabled).lower()))
        if available:
            client = Client(name='Demo', phone='', email='', is_active=True)
            db.add(client)
            await db.flush()
            db.add(ClientProfile(client_id=client.id, kind='mtproto', is_enabled=True))
        await db.commit()
        async def service(name):
            return (running if name == 'md-next-mtproto' else True), 'test'
        with patch('app.api.system._service_active', side_effect=service), \
                patch('app.api.system._port_listening', new=AsyncMock(return_value=True)), \
                patch('app.api.system._certificate_days', new=AsyncMock(return_value=30)):
            result = await get_system_health(db)
        row = next(item for item in result['checks'] if item['key'] == 'mtproto')
        assert row['status'] == expected
        assert row['name'] == 'Telegram MTProto'
