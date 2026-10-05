"""Bounded server checks shared by the panel and Telegram. No user URLs or secrets."""
import asyncio
import socket
import time
from datetime import datetime, timezone
import httpx
from app.services.route_probe import probe_current_exit
from app.services.warp import WarpService
from app.services.profiles import get_profile_settings
from app.services.cdn import probe_cdn

diagnostic_lock = asyncio.Lock()
last_report = None
last_run = 0.0


async def run_diagnostics(db):
    global last_report, last_run
    async with diagnostic_lock:
        if last_report and time.monotonic() - last_run < 30:
            return last_report
        checks = []
        async def check(key, label, action):
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(action(), 20)
                checks.append({'key': key, 'name': label, 'ok': True, 'detail': result,
                               'elapsed_ms': round((time.monotonic()-start)*1000)})
            except Exception:
                checks.append({'key': key, 'name': label, 'ok': False,
                    'detail': 'Проверка не прошла. Проверьте настройки и журнал событий.',
                    'elapsed_ms': round((time.monotonic()-start)*1000)})
        async def dns():
            await asyncio.to_thread(socket.getaddrinfo, 'example.com', 443)
            return 'Системный DNS разрешает example.com'
        async def route():
            info = await probe_current_exit()
            return f"Выход: {info['ip']} · {info.get('country', '—')}"
        async def doh():
            async with httpx.AsyncClient(proxy='socks5://127.0.0.1:10808', timeout=10, trust_env=False) as client:
                r = await client.get('https://cloudflare-dns.com/dns-query', params={'name':'example.com','type':'A'}, headers={'accept':'application/dns-json'})
                r.raise_for_status()
                if r.json().get('Status') != 0 or not r.json().get('Answer'):
                    raise ValueError('DNS')
            return 'DoH через текущий Xray отвечает'
        async def warp():
            info = await WarpService.test_target(db)
            return f"Выход WARP: {info.get('ip', 'проверен')}"
        await check('dns', 'DNS сервера', dns)
        await check('route', 'Выбранный выход Xray', route)
        await check('doh', 'DNS через туннель', doh)
        await check('warp', 'Выбранный WARP', warp)
        settings = await get_profile_settings(db)
        if settings.get('cdn.enabled') == 'true' and settings.get('cdn.domain'):
            result = await probe_cdn(settings['cdn.domain'], settings['profiles.path.vless_xhttp_tls'])
            checks.append({'key':'cdn','name':'CDN HTTPS и отправка XHTTP', 'ok':result['ok'], 'detail':result['message'], 'elapsed_ms':None})
        else:
            checks.append({'key':'cdn','name':'CDN','ok':None,'detail':'CDN выключен или не настроен', 'elapsed_ms':None})
        last_report = {'checked_at':datetime.now(timezone.utc).isoformat(), 'scope':'server',
            'notice':'Это серверные проверки. Работа с телефона и в сети с белыми списками требует отдельного теста.', 'checks':checks}
        last_run = time.monotonic()
        return last_report
