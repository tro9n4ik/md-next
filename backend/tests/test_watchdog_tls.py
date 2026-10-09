import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.watchdog import WatchdogService, probe_tls_context


def test_probe_context_reuses_verified_trust_store():
    probe_tls_context.cache_clear()
    context = probe_tls_context()
    assert probe_tls_context() is context
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()['x509_ca'] > 0


@pytest.mark.asyncio
async def test_periodic_egress_probes_share_context_and_close_clients():
    service = WatchdogService()
    response = MagicMock(status_code=204)
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=client)
    manager.__aexit__ = AsyncMock(return_value=None)
    with patch('app.services.watchdog.probe_enabled', return_value=True), patch('app.services.watchdog.httpx.AsyncClient', return_value=manager) as factory:
        for _ in range(3):
            assert (await service._probe_node_egress(SimpleNamespace(id=1, host='192.0.2.1')))[0]
    assert manager.__aexit__.await_count == 3
    assert all(call.kwargs['verify'] is probe_tls_context() for call in factory.call_args_list)
    assert all(call.kwargs['trust_env'] is False for call in factory.call_args_list)
