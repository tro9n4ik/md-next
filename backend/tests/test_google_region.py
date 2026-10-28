import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import google_region as google
from app.services import gemini_monitor as monitor
from app.services.gemini_monitor import GeminiMonitor
from conftest import TestingSessionLocal
from test_gemini_monitor import prepare, seed, BLOCKED, LOADED


@pytest.mark.parametrize(
    "html,expected",
    [
        ('<input type="hidden" name="region" value="RU">', "RU"),
        ("<input value='de' name='region' type='hidden'/>", "DE"),
        (
            '<input type="hidden" name="region" value="RU"><input type="hidden" name="region" value="DE">',
            "",
        ),
        (
            '<input type="hidden" name="region" value="RU"><input type="hidden" name="region" value="">',
            "",
        ),
        ('<input type="hidden" name="region" value="ZZ">', ""),
        ('<input type="hidden" name="region" value="RUS">', ""),
        ('<input name="region" value="RU">', ""),
        ('<input type="text" name="region" value="RU">', ""),
        ('<script>"<input type=hidden name=region value=RU>"</script>', ""),
        ("<template><input type=hidden name=region value=RU></template>", ""),
        ("<!-- <input type=hidden name=region value=RU> -->", ""),
        ('<input type=hidden name=region value=RU><div class="g-recaptcha"></div>', ""),
        ("<html>Please sign in</html>", ""),
    ],
)
def test_only_unambiguous_hidden_region_counts(html, expected):
    assert google.parse_google_country(html) == expected


def transport_client(handler):
    original = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs.pop("proxy") == "socks5://127.0.0.1:23456"
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)

    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [403, 429, 500])
async def test_error_page_is_never_a_country_signal(code):
    with patch.object(
        google.httpx,
        "AsyncClient",
        transport_client(
            lambda r: httpx.Response(
                code, text="<input type=hidden name=region value=RU>"
            )
        ),
    ):
        result = await google.probe_google_region(23456, timeout=1)
    assert result["google_country"] == ""
    assert result["google_region_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    [
        "http://127.0.0.1/admin",
        "https://accounts.google.com.evil.test/v3/signin/identifier",
        "https://accounts.google.com:8443/v3/signin/identifier",
        "https://accounts.google.com/v3/signin/challenge/recaptcha",
        "https://name:secret@accounts.google.com/v3/signin/identifier",
    ],
)
async def test_redirects_cannot_change_endpoint_or_send_credentials(destination):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": destination})

    with patch.object(google.httpx, "AsyncClient", transport_client(handler)):
        result = await google.probe_google_region(23456, timeout=1)
    assert calls == [google.GOOGLE_REGION_URL]
    assert result["google_country"] == ""


@pytest.mark.asyncio
async def test_oversize_body_and_network_error_are_unknown():
    for handler in [
        lambda r: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<input type=hidden name=region value=RU>"
            + b"x" * (google.MAX_BODY + 1),
        )
    ]:
        with patch.object(google.httpx, "AsyncClient", transport_client(handler)):
            assert (await google.probe_google_region(23456, timeout=1))[
                "google_country"
            ] == ""

    def error(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with patch.object(google.httpx, "AsyncClient", transport_client(error)):
        assert (await google.probe_google_region(23456, timeout=1))[
            "google_country"
        ] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "country,page,expected",
    [
        ("RU", "loaded", "google_region_ru"),
        ("DE", "loaded", "page_loaded"),
        ("RU", "network", "google_region_ru"),
        ("DE", "network", "unknown"),
        ("RU", "blocked", "region_blocked"),
        ("", "loaded", "page_loaded"),
    ],
)
async def test_google_country_and_gemini_are_independent(country, page, expected):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "accounts.google.com":
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=f'<input value="{country}" name="region" type="hidden">',
            )
        if page == "network":
            raise httpx.ReadTimeout("timeout", request=request)
        if page == "blocked":
            return httpx.Response(302, headers={"location": "/unsupportedcountry"})
        return httpx.Response(200, text="<h1>Gemini</h1>")

    with patch.object(
        monitor.WarpService,
        "test_proxy",
        AsyncMock(return_value={**BLOCKED, "country": "NL"}),
    ), patch.object(google.httpx, "AsyncClient", transport_client(handler)):
        result = await monitor.probe_proxy(23456, timeout=1)
    assert result["status"] == expected
    assert result["google_country"] == country
    assert result["country"] == "NL"
    assert sorted(calls) == sorted([google.GOOGLE_REGION_URL, monitor.GEMINI_URL])
    assert (
        result["gemini_status"]
        == {"loaded": "page_loaded", "network": "unknown", "blocked": "region_blocked"}[
            page
        ]
    )


RU = {
    **LOADED,
    "status": "google_region_ru",
    "google_country": "RU",
    "google_region_status": "detected",
    "gemini_status": "page_loaded",
}


@pytest.mark.asyncio
async def test_two_ru_signals_rotate_persist_and_recheck_country():
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(
            db,
            blocked_checks=1,
            block_reason="google_region_ru",
            status="google_region_ru",
        )
        with patch.object(
            monitor,
            "test_node_proxy",
            AsyncMock(side_effect=[RU, {**LOADED, "google_country": "DE"}]),
        ) as probe, patch.object(
            monitor.WarpService,
            "control_node",
            AsyncMock(return_value={"changed": True}),
        ) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        rotate.assert_called_once()
        assert result["google_country"] == "DE" and result["status"] == "page_loaded"
        assert result["blocked_checks"] == 0
        assert len(result["rotation_attempts"]) == 1
        assert [call.args[1] for call in probe.call_args_list] == [40000, 40000]
        assert (await monitor._load_state(db, 1))["google_country"] == "DE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous,result", [("region_blocked", RU), ("google_region_ru", BLOCKED)]
)
async def test_different_signal_types_do_not_count_as_two_confirmations(
    previous, result
):
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(db, blocked_checks=1, block_reason=previous)
        with patch.object(
            monitor, "test_node_proxy", AsyncMock(return_value=result)
        ), patch.object(monitor.WarpService, "control_node", AsyncMock()) as rotate:
            state = await GeminiMonitor().check(db, automatic=True)
        assert state["blocked_checks"] == 1
        rotate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [False, True])
async def test_manual_ru_and_first_automatic_ru_do_not_rotate(automatic):
    async with TestingSessionLocal() as db:
        await prepare(db)
        with patch.object(
            monitor, "test_node_proxy", AsyncMock(return_value=RU)
        ), patch.object(monitor.WarpService, "control_node", AsyncMock()) as rotate:
            result = await GeminiMonitor().check(db, automatic=automatic)
        assert result["google_country"] == "RU"
        assert result["blocked_checks"] == 1
        rotate.assert_not_called()


@pytest.mark.asyncio
async def test_network_failure_clears_previous_google_country_and_confirmations():
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(
            db, google_country="RU", blocked_checks=1, block_reason="google_region_ru"
        )
        with patch.object(
            monitor, "test_node_proxy", AsyncMock(side_effect=OSError("network"))
        ), patch.object(monitor.WarpService, "control_node", AsyncMock()) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        assert not result["google_country"] and result["blocked_checks"] == 0
        rotate.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_is_propagated():
    async def cancel(request):
        raise asyncio.CancelledError

    with patch.object(google.httpx, "AsyncClient", transport_client(cancel)):
        with pytest.raises(asyncio.CancelledError):
            await google.probe_google_region(23456, timeout=1)


@pytest.mark.asyncio
async def test_non_html_response_does_not_supply_region():
    with patch.object(
        google.httpx,
        "AsyncClient",
        transport_client(
            lambda r: httpx.Response(
                200, text="<input type=hidden name=region value=RU>"
            )
        ),
    ):
        result = await google.probe_google_region(23456, timeout=1)
    assert not result["google_country"]
    assert "HTML" in result["google_region_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [[9999], [9000, 9100, 9200]])
async def test_ru_signal_obeys_existing_cooldown_and_budget(attempts):
    async with TestingSessionLocal() as db:
        await prepare(db)
        await seed(
            db,
            blocked_checks=1,
            block_reason="google_region_ru",
            rotation_attempts=attempts,
        )
        with patch.object(monitor.time, "time", return_value=10000), patch.object(
            monitor, "test_node_proxy", AsyncMock(return_value=RU)
        ), patch.object(monitor.WarpService, "control_node", AsyncMock()) as rotate:
            result = await GeminiMonitor().check(db, automatic=True)
        rotate.assert_not_called()
        assert result["blocked_checks"] == 2
        assert result["rotation_note"]


@pytest.mark.asyncio
async def test_probe_deadline_is_bounded():
    async def delayed(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    with patch.object(google.httpx, "AsyncClient", transport_client(delayed)):
        result = await google.probe_google_region(23456, timeout=0.01)
    assert not result["google_country"]
    assert result["google_region_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("trace timeout"), ValueError("no IPv4")])
@pytest.mark.parametrize("country", ["DE", "RU"])
async def test_cloudflare_failure_keeps_google_result_without_rotation_signal(failure, country):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        if request.url.host == "accounts.google.com":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<input type="hidden" name="region" value="{country}">')
        return httpx.Response(302, headers={"location": "/unsupportedcountry"})

    with patch.object(monitor.WarpService, "test_proxy", AsyncMock(side_effect=failure)), \
         patch.object(google.httpx, "AsyncClient", transport_client(handler)):
        result = await monitor.probe_proxy(23456, timeout=1)
    assert sorted(calls) == ["accounts.google.com", "gemini.google.com"]
    assert result["google_country"] == country
    assert result["gemini_status"] == "region_blocked"
    assert result["status"] == "unknown"
    assert result["warp"] == ""
    assert type(failure).__name__ in result["message"]
