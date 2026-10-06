from unittest import mock
from unittest.mock import Mock

import pytest

from app.services.route_probe import XRAY_SOCKS_PROXY, probe_current_exit


@pytest.mark.asyncio
async def test_probe_uses_xray_socks_and_parses_cloudflare_trace():
    client = mock.AsyncMock()
    response = Mock(status_code=200, text="fl=abc\nip=203.0.113.7\nloc=RU\nwarp=off\n")
    client.get.return_value = response
    manager = mock.AsyncMock()
    manager.__aenter__.return_value = client

    with mock.patch("app.services.route_probe.httpx.AsyncClient", return_value=manager) as make_client:
        result = await probe_current_exit()

    assert result == {"ip": "203.0.113.7", "country": "RU", "warp": "off"}
    assert make_client.call_args.kwargs["proxy"] == XRAY_SOCKS_PROXY
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_rejects_invalid_ip_response():
    client = mock.AsyncMock()
    response = Mock(status_code=200, text="ip=not-an-ip\n")
    client.get.return_value = response
    manager = mock.AsyncMock()
    manager.__aenter__.return_value = client

    with mock.patch("app.services.route_probe.httpx.AsyncClient", return_value=manager):
        with pytest.raises(RuntimeError, match="корректный внешний IP"):
            await probe_current_exit()
