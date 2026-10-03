from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from app.services.watchdog import WatchdogService


@pytest.mark.asyncio
async def test_country_uses_node_proxy_and_caches_without_changing_health():
    service=WatchdogService()
    node=SimpleNamespace(id=7, country_code=None, is_active=True, status='healthy')
    response=SimpleNamespace(text='ip=198.51.100.1\nloc=DE\nwarp=off\n',raise_for_status=lambda:None)
    client=AsyncMock();client.get.return_value=response
    with patch('app.services.watchdog.probe_enabled',return_value=True), patch('app.services.watchdog.httpx.AsyncClient') as factory:
        factory.return_value.__aenter__.return_value=client
        await service._update_country(node)
        await service._update_country(node)
        assert factory.call_args.kwargs['proxy'].startswith('socks5://127.0.0.1:')
    client.get.assert_awaited_once()
    assert node.country_code=='DE' and node.is_active and node.status=='healthy'


@pytest.mark.asyncio
async def test_country_failure_keeps_existing_country_and_health():
    service=WatchdogService()
    node=SimpleNamespace(id=7,country_code='DE',is_active=True,status='healthy')
    with patch('app.services.watchdog.probe_enabled',return_value=True),patch('app.services.watchdog.httpx.AsyncClient',side_effect=RuntimeError('unavailable')):
        await service._update_country(node)
    assert node.country_code=='DE' and node.is_active and node.status=='healthy'
