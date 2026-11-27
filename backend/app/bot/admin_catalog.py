"""Разрешённые операции владельца. Произвольных HTTP-запросов из чата нет."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    key: str
    section: str
    title: str
    method: str
    path: str
    current: str = ""
    hint: str = ""


SECTIONS = {
    "clients": "👥 Клиенты и группы", "nodes": "🌍 Ноды и выход в интернет",
    "protocols": "🔌 Протоколы", "dns": "🧭 DNS", "routing": "🔀 Маршрутизация",
    "warp": "☁️ WARP", "cdn": "🛡 Обход БС", "settings": "⚙️ Настройки",
    "users": "👤 Субадминистраторы", "backups": "💾 Резервные копии",
    "system": "🩺 Обслуживание", "migration": "🚚 Переезд на VPS", "security": "🔐 Безопасность",
}


def op(key, section, title, method, path, current="", hint=""):
    return Operation(key, section, title, method, "/api/v1/" + path,
                     "/api/v1/" + current if current else "", hint)


OPERATIONS = {item.key: item for item in (
    op("groups", "clients", "Группы клиентов", "GET", "client-groups"),
    op("group_new", "clients", "Создать группу", "POST", "client-groups"),
    op("group_edit", "clients", "Изменить группу", "PUT", "client-groups/{group_id}", "client-groups"),
    op("group_delete", "clients", "Удалить группу", "DELETE", "client-groups/{group_id}", hint="Клиенты сохранятся без группы."),
    op("client_group", "clients", "Переместить клиента в группу", "PUT", "clients/{client_id}/group"),
    op("client_exit", "clients", "Основной маршрут клиента", "PUT", "clients/{client_id}/exit-route", "clients/{client_id}/exit-route"),
    op("client_routes", "clients", "Страны в подписке клиента", "PUT", "clients/{client_id}/subscription-routes", "clients/{client_id}/subscription-routes"),
    op("client_access", "clients", "Протоколы и блокировка рекламы", "PUT", "clients/{client_id}/access", "clients/{client_id}/profiles"),
    op("profile_regenerate", "clients", "Перевыпустить ключ протокола", "POST", "clients/{client_id}/profiles/{profile_id}/regenerate", hint="Старое подключение этого протокола перестанет работать."),
    op("client_delete", "clients", "Удалить клиента", "DELETE", "clients/{client_id}", hint="Подписка и подключения клиента будут удалены."),
    op("client_sub", "clients", "Перевыпустить ссылку подписки", "POST", "clients/{client_id}/sub/regenerate", hint="Старая ссылка перестанет работать."),
    op("templates", "clients", "Шаблоны подписок", "GET", "operations/templates"),
    op("templates_edit", "clients", "Изменить шаблоны", "PUT", "operations/templates", "operations/templates"),
    op("nodes", "nodes", "Все ноды", "GET", "nodes"),
    op("node", "nodes", "Карточка ноды", "GET", "nodes/{node_id}"),
    op("node_new", "nodes", "Подключить новую ноду", "POST", "nodes/invites", hint="Получите команду установки. Выполните её на сервере новой ноды."),
    op("invites", "nodes", "Приглашения нод", "GET", "nodes/invites"),
    op("invite_revoke", "nodes", "Отозвать приглашение", "DELETE", "nodes/invites/{invite_id}"),
    op("invite_clean", "nodes", "Очистить историю приглашений", "DELETE", "nodes/invites/history"),
    op("node_toggle", "nodes", "Включить или отключить ноду", "PUT", "nodes/{node_id}/toggle"),
    op("node_hosting", "nodes", "Ссылка на хостера", "PATCH", "nodes/{node_id}/hosting", "nodes/{node_id}"),
    op("node_order", "nodes", "Порядок резервирования", "POST", "nodes/reorder", "nodes"),
    op("node_delete", "nodes", "Удалить ноду", "DELETE", "nodes/{node_id}", hint="Нода будет отключена от панели. Её сервер не удаляется."),
    op("node_control", "nodes", "Состояние агента ноды", "GET", "nodes/{node_id}/control"),
    op("node_command", "nodes", "Команда агенту ноды", "POST", "nodes/{node_id}/control/commands"),
    op("route", "nodes", "Текущий маршрут", "GET", "cluster/route"),
    op("route_check", "nodes", "Проверить внешний IP", "POST", "cluster/route/check"),
    op("active_node", "nodes", "Сменить основной выход", "PUT", "cluster/active-node", hint="Подключения клиентов могут кратковременно прерваться."),
    op("failover", "nodes", "Автопереключение", "PUT", "cluster/failover", "cluster/failover"),
    op("history", "nodes", "История состояния ноды", "GET", "operations/history/{node_id}"),
    op("protocols", "protocols", "Настроенные протоколы", "GET", "settings/protocols"),
    op("protocols_edit", "protocols", "Настроить протоколы", "PUT", "settings/protocols", "settings/protocols", "Изменение адресов и параметров подключения может потребовать обновления подписок."),
    op("sni", "protocols", "Проверить домен Reality", "POST", "settings/protocols/sni/check"),
    op("dns", "dns", "Текущие настройки DNS", "GET", "settings/dns"),
    op("dns_edit", "dns", "Настроить DNS", "PUT", "settings/dns", "settings/dns"),
    op("rules", "routing", "Правила маршрутизации", "GET", "routing/rules"),
    op("rule_new", "routing", "Добавить правило", "POST", "routing/rules"),
    op("rule_delete", "routing", "Удалить правило", "DELETE", "routing/rules/{rule_id}"),
    op("rules_apply", "routing", "Применить правила", "POST", "routing/apply"),
    op("warp_status", "warp", "Состояние WARP", "GET", "warp/status"),
    op("warp_setup", "warp", "Установить WARP", "POST", "warp/setup"),
    op("warp_usage", "warp", "Как использовать WARP", "PUT", "warp/usage", "warp/usage"),
    op("warp_ipv4", "warp", "Использование IPv4", "PUT", "warp/ipv4"),
    op("warp_rotate", "warp", "Сменить IP WARP", "POST", "warp/rotate-ip"),
    op("warp_test", "warp", "Проверить соединение WARP", "POST", "warp/test"),
    op("warp_presets", "warp", "Доступные наборы правил", "GET", "warp/presets"),
    op("warp_preset_add", "warp", "Применить набор правил", "POST", "warp/presets/apply"),
    op("warp_preset_remove", "warp", "Удалить набор правил", "POST", "warp/presets/remove"),
    op("uplink_status", "warp", "Канал к нодам через WARP", "GET", "warp/uplink"),
    op("uplink", "warp", "Настроить канал к нодам", "PUT", "warp/uplink", "warp/uplink"),
    op("uplink_prepare", "warp", "Подготовить канал к нодам", "POST", "warp/uplink/prepare"),
    op("uplink_check", "warp", "Проверить канал к нодам", "POST", "warp/uplink/check"),
    op("gemini", "warp", "Мониторинг Gemini", "PUT", "warp/gemini/monitor", "warp/gemini/monitor"),
    op("gemini_check", "warp", "Проверить Gemini", "POST", "warp/gemini/check"),
    op("gemini_rules", "warp", "Подобрать правила через Gemini", "POST", "warp/gemini/generate"),
    op("cdn", "cdn", "Текущая настройка обхода БС", "GET", "cdn"),
    op("cdn_edit", "cdn", "Настроить обход БС", "PUT", "cdn", "cdn"),
    op("cdn_check", "cdn", "Проверить домен обхода БС", "POST", "cdn/check", "cdn"),
    op("subscription", "settings", "Настройка подписок", "PUT", "settings/subscription", "settings/subscription"),
    op("telegram", "settings", "Настройка уведомлений Telegram", "PUT", "settings/telegram", "settings/telegram", "Смена токена, владельца или выхода может разорвать связь с этим ботом."),
    op("telegram_test", "settings", "Тест уведомлений", "POST", "settings/telegram/test"),
    op("placeholder", "settings", "Сайт-заглушка", "GET", "settings/placeholder"),
    op("placeholder_edit", "settings", "Настроить заглушку", "PUT", "settings/placeholder", "settings/placeholder"),
    op("placeholder_generate", "settings", "Создать заглушку", "POST", "settings/placeholder/generate"),
    op("placeholder_restore", "settings", "Восстановить заглушку", "POST", "settings/placeholder/restore"),
    op("users", "users", "Список субадминистраторов", "GET", "users"),
    op("user_new", "users", "Создать субадминистратора", "POST", "users"),
    op("user_edit", "users", "Права, доступ и пароль", "PUT", "users/{user_id}", "users"),
    op("backups", "backups", "Сохранённые копии", "GET", "operations/backups"),
    op("backup_create", "backups", "Создать зашифрованную копию", "POST", "operations/backups"),
    op("backup_download", "backups", "Скачать копию", "GET", "operations/backups/{name}"),
    op("restore_preview", "backups", "Проверить копию для восстановления", "POST", "operations/restore/preview", hint="Прикрепите файл копии в поле «Файл копии». Сначала будет показан состав изменений."),
    op("restore", "backups", "Восстановить проверенную копию", "POST", "operations/restore", hint="Содержимое панели будет заменено. Сначала выполните проверку копии и получите код подтверждения."),
    op("system_info", "system", "Информация о сервере", "GET", "system/info"),
    op("system_stats", "system", "Нагрузка и ресурсы", "GET", "system/stats"),
    op("system_health", "system", "Состояние сервисов", "GET", "system/health"),
    op("diagnostics", "system", "Полная диагностика", "POST", "operations/diagnostics"),
    op("events", "system", "Журнал событий", "GET", "events"),
    op("updates", "system", "Версия и состояние обновления", "GET", "system/updates"),
    op("components", "system", "Компоненты сервера", "GET", "system/updates/components"),
    op("update_check", "system", "Проверить обновления", "POST", "system/updates/check"),
    op("update_install", "system", "Установить обновление", "POST", "system/updates/install", hint="Панель и бот кратковременно перезапустятся. Укажите полный commit из проверки обновлений."),
    op("migration_source", "migration", "Готовность к переезду", "GET", "migration/source"),
    op("migration_jobs", "migration", "Состояние переезда", "GET", "migration/jobs"),
    op("migration_fingerprint", "migration", "Получить отпечаток SSH", "POST", "migration/fingerprint", hint="Сверьте отпечаток с консолью хостера перед переездом."),
    op("migration_start", "migration", "Управление переездом", "POST", "migration/jobs", hint="Перед активацией нового VPS проверьте клиентские подключения и DNS."),
    op("migration_packages", "migration", "Комплекты для переезда", "GET", "migration/packages"),
    op("migration_export", "migration", "Подготовить комплект", "POST", "migration/packages"),
    op("migration_download", "migration", "Скачать комплект", "GET", "migration/packages/{name}"),
    op("migration_delete", "migration", "Удалить комплект", "DELETE", "migration/packages/{name}"),
    op("account", "security", "Учётная запись владельца", "GET", "auth/me"),
    op("password", "security", "Изменить пароль панели", "PUT", "auth/password"),
    op("totp_setup", "security", "Подготовить двухфакторную защиту", "POST", "auth/2fa/setup"),
    op("totp_verify", "security", "Подтвердить двухфакторную защиту", "POST", "auth/2fa/verify"),
    op("totp_disable", "security", "Отключить двухфакторную защиту", "POST", "auth/2fa/disable"),
)}

LABELS = {
    "name": "Название", "username": "Логин", "password": "Пароль", "hosting_url": "Ссылка на хостера",
    "ttl_hours": "Приглашение действует, часов", "transport": "Управляющий канал", "node_id": "Нода",
    "client_id": "Клиент", "group_id": "Группа", "user_id": "Субадминистратор", "rule_id": "Правило",
    "invite_id": "Приглашение", "enabled": "Включено", "is_active": "Доступ разрешён",
    "profiles": "Протоколы", "ports": "Порты", "paths": "Пути", "reality": "Reality", "modes": "Режимы",
    "vless_reality_tcp": "VLESS Reality", "vless_xhttp_reality": "VLESS XHTTP Reality",
    "vless_xhttp_tls": "VLESS XHTTP TLS", "hysteria2": "Hysteria 2", "awg": "AmneziaWG",
    "mtproto": "Telegram-прокси", "cdn": "Обход БС", "domain": "Домен", "mode": "Режим",
    "port": "Порт", "host": "Адрес сервера", "color": "Цвет", "action": "Действие",
    "remote_type": "Удалённый DNS: протокол", "remote_domain": "Удалённый DNS: адрес DoH",
    "remote_ip": "Удалённый DNS: IP", "domestic_type": "Локальный DNS: протокол",
    "domestic_domain": "Локальный DNS: адрес DoH", "domestic_ip": "Локальный DNS: IP",
    "domain_strategy": "Обработка доменов", "fake_dns": "Fake DNS", "domain_or_ip": "Домен или IP",
    "target_node_id": "Выходная нода", "description": "Описание", "usage": "Использование WARP",
    "key": "Ключ или набор правил", "ipv4_only": "Только IPv4", "ids": "Порядок ID нод",
    "ping_threshold_ms": "Порог задержки, мс", "failure_count": "Ошибок до переключения",
    "interval_s": "Интервал проверки, секунд", "failback": "Возвращаться на основной выход",
    "failback_stable_checks": "Успешных проверок до возврата", "cooldown_s": "Пауза между переключениями, секунд",
    "fallback_action": "Если все ноды недоступны", "view_all_clients": "Видит всех клиентов",
    "can_issue_subscriptions": "Может создавать подписки", "can_delete_subscriptions": "Может удалять подписки",
    "can_pause_subscriptions": "Может приостанавливать доступ", "data": "Файл копии",
    "token": "Код подтверждения", "confirm": "Подтверждение", "commit": "Commit обновления",
    "github_token": "Токен GitHub (необязательно)", "admin_id": "Telegram ID владельца",
    "notify_failover": "Уведомления о переключениях", "notify_system": "Системные уведомления",
    "notify_clients": "Уведомления о клиентах", "notify_quota": "Уведомления о лимитах",
    "notify_expiry": "Уведомления об окончании срока", "proxy_url": "Прокси бота",
    "ssh_user": "Пользователь SSH", "ssh_password": "Пароль SSH", "fingerprint": "Отпечаток SSH",
    "confirm_protocol_tests": "Подтверждаю внешнюю проверку протоколов", "server_address": "Адрес подключения",
    "server_name": "Имя сервера (SNI)", "target": "Цель подключения", "short_id": "Short ID",
    "public_key": "Публичный ключ", "private_key": "Приватный ключ", "flow": "Flow",
    "templates": "Шаблоны", "node_ids": "Доступные страны: ID нод", "inherit": "Общий маршрут панели",
    "confirm_link_identity_change": "Разрешить изменение адресов подключения", "domains": "Домены для проверки",
    "email": "Электронная почта", "expected_country": "Ожидаемая страна", "kind": "Тип команды",
    "notify_node_down": "Уведомления о недоступных нодах", "use_node": "Telegram через ноду",
    "old_password": "Текущий пароль", "new_password": "Новый пароль", "current_code": "Текущий код 2FA",
    "code": "Одноразовый код 2FA", "html": "HTML-файл заглушки", "status": "Состояние",
    "prompt": "Задача для Gemini", "api_key": "Ключ API Gemini",
    "period": "Срок", "monthly_traffic_limit": "Месячный трафик",
    "adblock_enabled": "Блокировать рекламу", "profile_id": "Протокол клиента",
    "message": "Сообщение", "id": "ID", "country_code": "Страна", "is_online": "В сети",
    "last_seen": "Последняя связь", "ping_ms": "Задержка, мс", "priority": "Приоритет",
    "created_at": "Создано", "expires_at": "Действует до", "size": "Размер, байт",
    "used_at": "Использовано", "revoked": "Отозвано", "ok": "Успешно", "phase": "Этап",
    "installed_version": "Установленная версия", "installed_commit": "Установленный commit",
    "latest_commit": "Доступный commit", "update_available": "Доступно обновление", "running": "Выполняется",
}

VALUE_LABELS = {
    "manual": "Вручную", "auto": "Автоматически", "direct": "Напрямую", "keep": "Сохранить маршрут",
    "off": "Выключено", "rules": "По правилам", "all": "Весь трафик", "proxy": "Через прокси",
    "block": "Заблокировать", "week": "Неделя", "month": "Месяц", "year": "Год", "unlimited": "Без срока",
    "emerald": "Зелёный", "blue": "Синий", "violet": "Фиолетовый", "amber": "Жёлтый", "rose": "Розовый", "slate": "Серый",
}


def label(path):
    return " / ".join(LABELS.get(part, part.replace("_", " ")) for part in path)


def needs_confirmation(key):
    operation = OPERATIONS[key]
    return operation.method == "DELETE" or key in {
        "profile_regenerate", "client_sub", "restore", "migration_start", "update_install",
        "active_node", "node_toggle", "failover", "protocols_edit", "dns_edit", "rules_apply",
        "warp_disconnect", "warp_setup", "warp_register", "warp_mode", "warp_target", "uplink",
        "cdn_edit", "placeholder_restore", "password", "totp_disable", "telegram",
    }
