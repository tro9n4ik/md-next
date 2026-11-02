"""Source-side migration planning. No remote calls or writes to runtime state."""
import ipaddress
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select, func

from app.models.client import Client, ClientProfile
from app.models.node import Node
from app.services.profiles import get_profile_settings


def address_kind(value):
    value = (value or '').strip()
    if not value:
        return 'missing', ''
    try:
        raw_ip = ipaddress.ip_address(value)
        return ('ip' if raw_ip.is_global else 'local'), str(raw_ip)
    except ValueError:
        pass
    try:
        parsed = urlsplit(value if '://' in value else '//'+value)
        host = (parsed.hostname or '').rstrip('.').lower()
        if parsed.username or parsed.password or not host:
            return 'invalid', ''
        if host == 'localhost' or host.endswith('.localhost'):
            return 'local', host
        try:
            ip = ipaddress.ip_address(host)
            return ('ip' if ip.is_global else 'local'), host
        except ValueError:
            host = host.encode('idna').decode('ascii')
            if '.' not in host or any(not part or len(part)>63 or part.startswith('-') or part.endswith('-') or
                                      any(not (ch.isascii() and (ch.isalnum() or ch == '-')) for ch in part)
                                      for part in host.split('.')):
                return 'invalid', ''
            return 'domain', host
    except (ValueError, UnicodeError):
        return 'invalid', ''


async def source_report(db):
    settings = await get_profile_settings(db)
    counts = dict((await db.execute(select(ClientProfile.kind, func.count()).group_by(ClientProfile.kind))).all())
    clients = (await db.execute(select(func.count()).select_from(Client))).scalar_one()
    pinned = (await db.execute(select(func.count()).select_from(Client).where(Client.exit_node_id.is_not(None)))).scalar_one()
    nodes = (await db.execute(select(func.count()).select_from(Node))).scalar_one()
    addresses = []
    def address(key, title, value, profiles):
        kind, host = address_kind(value)
        addresses.append({'key': key, 'title': title, 'host': host, 'kind': kind, 'profiles': profiles,
                          'status': 'ok' if kind == 'domain' else 'warning' if kind == 'ip' else 'blocked',
                          'message': 'Можно сохранить адрес при переключении DNS.' if kind == 'domain' else
                          'В профилях указан IP. Нужны обновление профилей или временный мост со старого VPS.' if kind == 'ip' else
                          'Публичный адрес не задан или некорректен. Проверьте его до переноса.'})
    host = settings.get('protocol.reality.server_address', '')
    address('panel', 'Панель и личные ссылки', os.getenv('PANEL_PUBLIC_URL', ''), 0)
    if counts:
        address('profiles', 'VPN-профили и Telegram MTProto', host, sum(counts.values()))
    if settings.get('cdn.enabled') == 'true':
        address('cdn', 'Обход БС / CDN', settings.get('cdn.domain', ''), counts.get('vless_xhttp_tls', 0))
    checks = [
        {'key': 'installation_key', 'title': 'Ключ установки', 'status': 'ok' if os.getenv('JWT_SECRET_KEY') else 'blocked',
         'message': 'JWT_SECRET_KEY задан. Его значение необходимо сохранить вместе с исходным .env; оно не включено в отчёт.' if os.getenv('JWT_SECRET_KEY') else 'JWT_SECRET_KEY не задан: перенос зашифрованных секретов нельзя подтвердить.'},
        {'key': 'source_addresses', 'title': 'Адреса подключения',
         'status': 'blocked' if any(a['status'] == 'blocked' for a in addresses) else 'warning' if any(a['status'] == 'warning' for a in addresses) else 'ok',
         'message': 'Проверены текущие адреса, из которых панель формирует новые профили. Старые импорты и ручные изменения в приложениях проверить с сервера нельзя.'},
        {'key': 'target', 'title': 'Новый VPS', 'status': 'pending', 'message': 'Не проверен. Подготовка текущей панели не подтверждает готовность нового сервера.'},
    ]
    return {'format': 1, 'phase': 'source_preflight', 'checked_at': datetime.now(timezone.utc).isoformat(),
            'can_switch': False, 'source_checks_passed': all(c['status'] == 'ok' for c in checks if c['key'] != 'target'),
            'counts': {'clients': clients, 'profiles': sum(counts.values()), 'nodes': nodes, 'pinned_clients': pinned},
            'protocols': counts, 'addresses': addresses, 'checks': checks,
            'preserve': [
                'Полный снимок SQLite со всеми таблицами, включая учётные записи, группы и привязки Telegram.',
                'Исходный backend/.env: JWT_SECRET_KEY и остальные ключи установки.',
                'Ключи Reality, UUID, short ID, SNI, порты и пути XHTTP.',
                'Ключи AmneziaWG, адреса интерфейса и выданные клиентские конфигурации.',
                'Секреты MTProto, публичный порт и домен FakeTLS.',
                'Сертификаты с закрытыми ключами, настройки Nginx и Xray, системные службы.',
                'Данные сайта-заглушки, DNS/CDN, маршруты, ноды и параметры WARP.',
            ],
            'steps': [
                'Подготовить новый VPS той же версии и проверить порты и доступность нод.',
                'Запустить пробный SSH-перенос полного зашифрованного комплекта, сохранив пароль отдельно.',
                'До смены DNS проверить панель, подписки и каждый используемый протокол на новом VPS.',
                'Остановить изменения на старой панели, выполнить финальную синхронизацию; избежать одновременной работы двух Telegram-ботов.',
                'Вручную переключить DNS и проверить подключения из внешней сети.',
                'Оставить старый VPS для отката. После записи новых данных на новом сервере обратный перенос требует согласования баз.',
            ]}
