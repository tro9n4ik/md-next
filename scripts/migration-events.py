"""Public migration protocol: only catalogued messages cross the root/API boundary.

Never return exception text, remote stdout or stderr to the browser. In particular,
installer output can contain initial passwords and configuration keys.
"""
import json
import socket
import subprocess

PREFIX = 'MDNEXT_EVENT '
MESSAGES = {
    'MIG_INPUT': 'Проверьте адрес, порт и обязательные поля запроса.',
    'MIG_INTERNAL': 'Внутренняя ошибка переезда. Проверьте этап и состояние серверов.',
    'MIG_BUSY': 'Другая операция обслуживания уже выполняется.',
    'MIG_INTERRUPTED': 'Процесс прерван. Проверьте целевой VPS перед повтором.',
    'MIG_MEMORY': 'Недостаточно доступной RAM для полного комплекта. Освободите память и повторите подготовку.',
    'MIG_SOURCE_CONFIG': 'Не удалось собрать исходные конфигурации. Проверьте обязательные файлы, пути сертификатов и размер комплекта.',
    'MIG_SSH_CONNECT': 'Не удалось подключиться по SSH. Проверьте адрес, порт и сеть.',
    'MIG_SSH_AUTH': 'SSH не принял пароль root. Проверьте учётные данные.',
    'MIG_SSH_KEY': 'Ключ SSH изменился. Пароль серверу не отправлен. Заново сверьте отпечаток.',
    'MIG_TIMEOUT': 'Истекло время операции. Проверьте VPS; удалённый процесс мог продолжить работу.',
    'MIG_REMOTE_EXIT': 'Удалённый этап завершился ошибкой. Проверьте код и последний подтверждённый шаг.',
    'MIG_SAME_SERVER': 'В качестве нового VPS выбран исходный сервер.',
    'MIG_TARGET_BINDING': 'Адрес, ключ или пароль комплекта не совпадает с предыдущей подготовкой.',
    'MIG_SOURCE_ADDRESS': 'Для финального переноса нужен публичный домен клиентских профилей.',
    'MIG_STATE': 'Действие недоступно в текущем состоянии переезда.',
    'MIG_BUILD': 'Сборка установки не определена или не прошла проверку GitHub.',
    'MIG_TARGET_OS': 'Поддерживаются Debian 12/13 и Ubuntu 22.04/24.04/26.04.',
    'MIG_TARGET_OCCUPIED': 'На VPS есть чужая или неподтверждённая установка. Автоматическая перезапись запрещена.',
    'MIG_TARGET_PORTS': 'На целевом VPS заняты порты 80, 443 или 8000.',
    'MIG_TARGET_RESOURCES': 'Нужно не менее 2 ГиБ RAM со swap и 5 ГиБ свободного диска.',
    'MIG_APT_LOCK': 'Пакетный менеджер занят дольше допустимого. Дождитесь завершения обновлений.',
    'MIG_APT_UPDATE': 'Обновление Ubuntu/Debian не завершилось. Проверьте сеть и состояние пакетов.',
    'MIG_REBOOT_REQUIRED': 'Обновление ОС завершено. Перезагрузите новый VPS и повторите подготовку; установка панели ещё не запущена.',
    'MIG_INSTALL': 'Установщик панели завершился ошибкой.',
    'MIG_INSTALL_AWG': 'Не удалось подготовить конфигурацию AmneziaWG перед разделением прав.',
    'MIG_RESTORE': 'Восстановление не прошло проверку. Службы целевого VPS остановлены; проверьте возможность отмены.',
    'MIG_BUNDLE': 'Пароль комплекта неверен, комплект повреждён или его состав недопустим.',
    'MIG_VERIFY': 'Проверка VPN или HTTPS на новом VPS не прошла.',
    'MIG_ADGUARD': 'На новом VPS требуется отдельно подготовить AdGuard Home.',
    'MIG_WARP': 'Не удалось подготовить WARP на новом VPS.',
    'TARGET_PREFLIGHT': 'Проверяем ОС, ресурсы, порты и принадлежность установки.',
    'TARGET_APT_WAIT': 'Ожидаем освобождения пакетного менеджера.',
    'TARGET_APT_UPDATE': 'Обновляем списки пакетов.',
    'TARGET_APT_UPGRADE': 'Обновляем ОС перед установкой панели.',
    'TARGET_BUILD': 'Проверяем подпись и CI исходной сборки.',
    'TARGET_INSTALL': 'Запускаем установку той же сборки в изолированном режиме.',
    'TARGET_RESUME': 'Продолжаем подтверждённую частичную установку этого переезда.',
    'TARGET_READY': 'Целевая установка подготовлена и остаётся изолированной.',
    'INSTALL_DEPS': 'Установка зависимостей и Nginx.',
    'INSTALL_AWG': 'Установка AmneziaWG.',
    'INSTALL_NODE': 'Установка Node.js.',
    'INSTALL_XRAY': 'Установка Xray.',
    'INSTALL_REPO': 'Копирование исходников панели.',
    'INSTALL_ROUTING': 'Подготовка маршрутизации AmneziaWG.',
    'INSTALL_BACKEND': 'Установка backend и миграции пустой базы.',
    'INSTALL_PLACEHOLDER': 'Подготовка сайта-заглушки.',
    'INSTALL_FRONTEND': 'Сборка интерфейса.',
    'INSTALL_SERVICES': 'Подготовка HTTPS и системных служб.',
    'INSTALL_READY': 'Ожидание готовности API.',
    'INSTALL_PRIVILEGES': 'Разделение прав системных компонентов.',
    'INSTALL_COMMIT': 'Сохранение номера сборки.',
}


class MigrationError(RuntimeError):
    def __init__(self, code, *, exit_code=None):
        self.code = code if code in MESSAGES else 'MIG_INTERNAL'
        self.exit_code = exit_code
        super().__init__(MESSAGES[self.code])


def public_error(error, fallback='MIG_INTERNAL'):
    code = getattr(error, 'code', None)
    if code not in MESSAGES:
        if isinstance(error, (socket.timeout, TimeoutError, subprocess.TimeoutExpired)):
            code = 'MIG_TIMEOUT'
        elif isinstance(error, BlockingIOError):
            code = 'MIG_BUSY'
        elif type(error).__name__ == 'AuthenticationException':
            code = 'MIG_SSH_AUTH'
        elif isinstance(error, MemoryError):
            code = 'MIG_MEMORY'
        else:
            code = fallback
    result = {'code': code, 'message': MESSAGES[code]}
    exit_code = getattr(error, 'exit_code', None)
    if type(exit_code) is int:
        result['exit_code'] = exit_code
    return result


def event(code):
    if code not in MESSAGES:
        raise ValueError('Unknown event code')
    return PREFIX + json.dumps({'code': code}, separators=(',', ':'))


def parse_event(line):
    if len(line) > 256 or not line.startswith(PREFIX):
        return None
    try:
        data = json.loads(line[len(PREFIX):])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != {'code'} or not isinstance(data['code'],str) or data['code'] not in MESSAGES:
        return None
    return {'code': data['code'], 'message': MESSAGES[data['code']]}
