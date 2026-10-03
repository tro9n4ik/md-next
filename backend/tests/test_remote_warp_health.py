from unittest.mock import AsyncMock, patch
import pytest
from app.api.system import get_system_health
from app.db.database import AsyncSessionLocal
from app.models.setting import Setting


@pytest.mark.asyncio
async def test_remote_warp_health_does_not_require_local_service():
    async with AsyncSessionLocal() as db:
        db.add(Setting(key='warp.usage',value='rules'));await db.commit()
        with patch('app.api.system.WarpService.status',new=AsyncMock(return_value={'remote':True,'installed':True,'service_active':None,'state':'Connected','name':'Германия'})):
            result=await get_system_health(db)
    warp=next(item for item in result['checks'] if item['key']=='warp')
    assert warp['status']=='ok'
    assert 'Германия' in warp['description']
