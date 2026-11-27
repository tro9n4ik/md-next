"""Полное меню владельца: разделы, выбор объектов, формы и подтверждения."""
import asyncio
import base64
import copy
import json
import logging
import os
import re
import secrets
import shlex
import sys
import time
from dataclasses import dataclass, field
from html import escape
from urllib.parse import urlencode, urlsplit

from aiogram import F
from aiogram.types import BufferedInputFile

from . import admin_api as api
from .admin_catalog import OPERATIONS, SECTIONS, VALUE_LABELS, label, needs_confirmation

logger = logging.getLogger(__name__)
TTL = 600
PAGE_SIZE = 7
buttons = {}
drafts = {}
running = set()
tasks = set()


def sys_module():
    return sys.modules[__name__]


def escaped_text(value, limit=3000):
    result, size = [], 0
    for char in str(value):
        encoded = escape(char)
        if size + len(encoded) > limit:
            result.append("…")
            break
        result.append(encoded)
        size += len(encoded)
    return "".join(result)


@dataclass
class Draft:
    key: str
    params: dict = field(default_factory=dict)
    fields: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    current: dict = field(default_factory=dict)
    waiting: tuple | None = None
    page: int = 0
    node_page: int | None = None
    context: str = ''
    node_state: tuple | None = None
    return_view: tuple | None = None
    expires: float = field(default_factory=lambda: time.monotonic() + TTL)


def clean():
    now = time.monotonic()
    for token, value in list(buttons.items()):
        if value["expires"] <= now:
            buttons.pop(token, None)
    for key, value in list(drafts.items()):
        if value.expires <= now:
            drafts.pop(key, None)


def editing(chat):
    clean()
    return (chat, chat) in drafts or chat in running


def cancel(actor, chat):
    drafts.pop((actor, chat), None)


def invalidate(chat, message, markup):
    keep = {b.callback_data for row in markup.inline_keyboard for b in row} if markup else set()
    for token, value in list(buttons.items()):
        if (value["chat"], value["message"]) == (chat, message) and "adm:b:" + token not in keep:
            buttons.pop(token, None)


async def draw(message, actor, title, text, rows):
    from .handlers import keyboard, show_screen
    clean()
    markup = []
    created = []
    for row in rows:
        output = []
        for text_label, action, value in row:
            if action == 'direct':
                if not isinstance(value,str) or not value.startswith(('ops:','sub:','md:')):
                    raise ValueError('Недопустимая кнопка.')
                output.append((text_label[:60],value))
                continue
            token = secrets.token_hex(8)
            buttons[token] = {"actor": actor, "chat": message.chat.id, "message": message.message_id,
                              "action": action, "value": value, "expires": time.monotonic() + TTL}
            created.append(token)
            output.append((text_label[:60], "adm:b:" + token))
        markup.append(output)
    markup.append([("🏠 Главное меню", "md:home")])
    result = await show_screen(message, f"<b>{escape(title)}</b>\n\n{text}", keyboard(*markup), edit=True)
    identifier = getattr(result, "message_id", message.message_id)
    for token in created:
        if token in buttons:
            buttons[token]["message"] = identifier


async def home(message, actor):
    cancel(actor, message.chat.id)
    rows = [[(title, "section", (key, 0))] for key, title in SECTIONS.items()]
    await draw(message, actor, "Управление панелью", "", rows)


def node_label(node):
    code = str(node.get('country_code') or node.get('country') or '').upper()
    flag = ''.join(chr(127397 + ord(char)) for char in code) if len(code) == 2 and code.isascii() and code.isalpha() and code not in {'XX', 'ZZ'} else '🌐'
    return flag + ' ' + str(node.get('name') or 'Нода').replace('\n', ' ')[:34]


async def nodes_home(message, actor):
    await nodes_list(message, actor)


async def nodes_list(message, actor, page=0):
    nodes = await api.call(actor, message.chat.id, 'nodes')
    route = await api.call(actor, message.chat.id, 'route')
    active_id = (route.get('active_node') or {}).get('id')
    nodes = sorted(nodes, key=lambda node: (node.get('priority', 0), node['id']))
    size = 3
    page = max(0, min(page, max(0, (len(nodes) - 1) // size)))
    rows = []
    for node in nodes[page * size:(page + 1) * size]:
        active = node['id'] == active_id
        enabled = node.get('is_enabled', True)
        status = 'пауза' if not enabled else ('активная' if node.get('status') == 'healthy' else 'активная · нет связи') if active else 'в сети' if node.get('status') == 'healthy' else 'нет связи'
        rows.append([(node_label(node) + ' · ' + status, 'nodes_list', page)])
        rows.append([
            ('● Активна' if active else 'Сделать активной', 'nodes_list' if active else 'node_action', page if active else ('active_node', node['id'], None, page)),
            ('Приостановить' if enabled else 'Включить', 'node_action', ('node_toggle', node['id'], not enabled, page)),
            ('Удалить', 'node_action', ('node_delete', node['id'], None, page)),
        ])
    nav = []
    if page:
        nav.append(('←', 'nodes_list', page - 1))
    if (page + 1) * size < len(nodes):
        nav.append(('→', 'nodes_list', page + 1))
    if nav:
        rows.append(nav)
    rows.append([('＋ Добавить', 'start', 'node_new'), ('Настройки', 'nodes_settings', 'main')])
    rows.append([('← Разделы', 'home', None)])
    await draw(message, actor, 'Ноды', f'Всего: {len(nodes)}' if nodes else 'Нод пока нет.', rows)


async def nodes_settings(message, actor, group):
    cancel(actor, message.chat.id)
    groups = {
        'route': ('Маршрут', ('route_check', 'failover', 'node_order')),
        'invites': ('Приглашения', ('invites', 'invite_revoke', 'invite_clean')),
        'other': ('Настройки нод', ('node_hosting', 'history', 'node_control', 'node_command')),
    }
    if group == 'main':
        rows = [[('Маршрут', 'nodes_settings', 'route'), ('Приглашения', 'nodes_settings', 'invites')],
                [('Хостер и обслуживание', 'nodes_settings', 'other')]]
        title = 'Настройки нод'
    else:
        title, keys = groups[group]
        rows = [[(OPERATIONS[key].title, 'start', key)] for key in keys]
    rows.append([('← Ноды', 'nodes_list', 0)])
    await draw(message, actor, title, '', rows)


async def node_action(message, actor, key, node_id, desired, page):
    if key not in {'active_node', 'node_toggle', 'node_delete'}:
        raise ValueError('Действие недоступно.')
    node = await api.call(actor, message.chat.id, 'node', {'node_id': node_id})
    if key == 'active_node' and not node.get('is_enabled', True):
        raise ValueError('Сначала включите ноду.')
    if key == 'node_toggle' and node.get('is_enabled', True) == desired:
        await nodes_list(message, actor, page)
        return
    fields = [(('node_id',), {'type': 'integer'})] if key == 'active_node' else []
    draft = Draft(key, params={} if key == 'active_node' else {'node_id': node_id},
                  fields=fields, values={'node_id': node_id} if fields else {},
                  node_page=page, context=node_label(node), node_state=(node.get('name'), node.get('host'), node.get('is_enabled', True)))
    drafts[(actor, message.chat.id)] = draft
    if key == 'node_toggle':
        route = await api.call(actor, message.chat.id, 'route')
        if desired or (route.get('active_node') or {}).get('id') != node_id:
            cancel(actor, message.chat.id)
            await safe_execute(message, actor, draft)
            return
    await review(message, actor, draft)


async def section(message, actor, key, page=0):
    cancel(actor, message.chat.id)
    if key == 'nodes':
        await nodes_home(message, actor)
        return
    from . import admin_compact
    if await admin_compact.section(sys_module(), message, actor, key):
        return
    items = [op for op in OPERATIONS.values() if op.section == key]
    rows = [[(op.title, "start", op.key)] for op in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
    navigation = []
    if page:
        navigation.append(("← Назад", "section", (key, page - 1)))
    if (page + 1) * PAGE_SIZE < len(items):
        navigation.append(("Далее →", "section", (key, page + 1)))
    if navigation:
        rows.append(navigation)
    rows.append([("← Все разделы", "home", None)])
    await draw(message, actor, SECTIONS[key], "", rows)


def secret(path):
    return any(part in {"password", "ssh_password", "private_key", "secret", "token", "github_token", "api_key", "data", "proxy_url", "otpauth_url", "old_password", "new_password", "current_code", "code"}
               or part.endswith("_secret") for part in path)


def short(value, path=()):
    if secret(path) and value not in (None, ""):
        return "•••••• (задано)"
    if value is None:
        return "не задано"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:90]
    if isinstance(value, str) and value in VALUE_LABELS:
        return VALUE_LABELS[value]
    return str(value).replace("\n", " ")[:90]


def readable(value, path=()):
    if secret(path):
        return [f"{label(path)}: {short(value, path)}"]
    if isinstance(value, dict):
        return [line for key, child in value.items() for line in readable(child, (*path, key))]
    if isinstance(value, list):
        return [line for i, child in enumerate(value) for line in readable(child, (*path, str(i + 1)))] or [f"{label(path)}: пусто"]
    return [f"{label(path)}: {str(value)[:500] if value is not None else '—'}"]


async def result_screen(message, actor, key, value, page=0):
    if key in {'nodes', 'node'}:
        await nodes_list(message, actor, page)
        return
    operation = OPERATIONS[key]
    if operation.section == 'nodes' and not isinstance(value, bytes):
        if key == 'node_new':
            await draw(message, actor, 'Новая нода', escape(str(value.get('name', 'Нода'))),
                       [[('Команда установки', 'invite_command', value['token'])], [('← Ноды', 'nodes_list', 0)]])
        elif key == 'route_check':
            await draw(message, actor, 'Выход в интернет', escaped_text(value.get('exit_ip', 'Не определён')),
                       [[('← Ноды', 'nodes_list', 0)]])
        elif key == 'node_control':
            states = {'standard': 'Обычное подключение', 'unpaired': 'Агент не привязан', 'waiting': 'Ожидание агента', 'online': 'Агент на связи', 'offline': 'Агент не отвечает'}
            await draw(message, actor, 'Агент ноды', states.get(value.get('state'), 'Статус неизвестен'),
                       [[('← Ноды', 'nodes_list', 0)]])
        elif key == 'history':
            availability = value.get('availability')
            text = 'За сутки: ' + (f'{availability:g}%' if isinstance(availability, (int, float)) else 'нет данных')
            if value.get('last_failure'):
                text += '\nПоследний сбой: ' + str(value['last_failure']).replace('T', ' ')[:16] + ' UTC'
            await draw(message, actor, 'Доступность ноды', escaped_text(text), [[('← Ноды', 'nodes_list', 0)]])
        elif operation.method != 'GET':
            await nodes_list(message, actor)
        else:
            entries = value if isinstance(value, list) else value.get('items', []) if isinstance(value, dict) else []
            page = max(0, min(page, max(0, (len(entries)-1)//5)))
            lines = [str(item.get('name') or item.get('message') or item.get('status') or 'Запись')[:100] for item in entries[page*5:(page+1)*5] if isinstance(item, dict)]
            rows = []
            nav = []
            if page:
                nav.append(('←', 'result', (key, value, page-1)))
            if (page+1)*5 < len(entries):
                nav.append(('→', 'result', (key, value, page+1)))
            if nav:
                rows.append(nav)
            rows.append([('← Ноды', 'nodes_list', 0)])
            await draw(message, actor, operation.title, escaped_text('\n'.join(lines)) or 'Нет записей.',
                       rows)
        return
    if isinstance(value, bytes):
        await draw(message, actor, operation.title, "Готово. Файл отправлен ниже. Сохраните его перед переходом на другой экран.",
                   [[("← В раздел", "section", (operation.section, 0))]])
        sent = await message.answer_document(BufferedInputFile(value, filename="md-next-backup.mdbackup" if key == "backup_download" else "md-next-migration.mdmove"))
        from .chat_screen import remember_attachment
        await remember_attachment(message, sent)
        return
    from . import admin_compact
    if await admin_compact.present(sys_module(),message,actor,key,value,page):
        return


SOURCES = {
    "node_id": ("nodes", "id"), "target_node_id": ("nodes", "id"),
    "client_id": ("clients", "id"), "group_id": ("client-groups", "id"),
    "user_id": ("users", "id"), "rule_id": ("routing/rules", "id"),
    "invite_id": ("nodes/invites", "id"),
}


async def choose(message, actor, draft, path, page=0, parameter=False):
    name = path[-1]
    if name == "profile_id":
        value = await api.request(actor, message.chat.id, "GET", f"/api/v1/clients/{int(draft.params['client_id'])}/profiles")
        rows = [[(item["label"], "param", (path, item["id"]))] for item in value["profiles"] if item["kind"] != "cdn"]
        rows.append([("Отмена", "section", ("clients", 0))])
        await draw(message, actor, "Протокол клиента", "Выберите подключение для перевыпуска ключа.", rows)
        return
    source, identity = SOURCES.get(name, ("migration/packages" if draft.key.startswith("migration_") else "operations/backups", "name"))
    value = await api.request(actor, message.chat.id, "GET", "/api/v1/" + source)
    if isinstance(value, dict):
        value = value.get("groups", value.get("items", value.get("clients", [])))
    if not isinstance(value, list):
        raise ValueError("Не удалось получить список объектов.")
    rows = []
    if not parameter:
        spec = dict(draft.fields)[path]
        if spec.get("nullable"):
            rows.append([("Без ноды / общий маршрут" if "node" in name else "Без группы", "value", (path, None))])
    for item in value[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        identifier = item[identity]
        title = item.get("name") or item.get("username") or item.get("domain_or_ip") or str(identifier)
        rows.append([(f"{title} · {identifier}", "param" if parameter else "value", (path, identifier))])
    nav = []
    if page:
        nav.append(("← Назад", "choose", (path, page - 1, parameter)))
    if (page + 1) * PAGE_SIZE < len(value):
        nav.append(("Далее →", "choose", (path, page + 1, parameter)))
    if nav:
        rows.append(nav)
    rows.append([("Отмена", "section", (OPERATIONS[draft.key].section, 0))])
    await draw(message, actor, label(path), "Выберите из списка." if value else "Список пока пуст.", rows)


async def start(message, actor, key):
    from .admin_compact import return_view
    draft = Draft(key,return_view=return_view(key))
    drafts[(actor, message.chat.id)] = draft
    await continue_start(message, actor, draft)


async def continue_start(message, actor, draft):
    operation = OPERATIONS[draft.key]
    missing = next((name for name in re.findall(r"{(\w+)}", operation.path) if name not in draft.params), None)
    if missing:
        await choose(message, actor, draft, (missing,), parameter=True)
        return
    if operation.method == "GET":
        cancel(actor, message.chat.id)
        await execute(message, actor, draft)
        return
    schema, root = api.body_schema(draft.key)
    current = await api.current_values(actor, message.chat.id, draft.key, draft.params)
    draft.fields = api.fields(schema, root, current)
    draft.current = api.initial_values(draft.fields, current)
    draft.values = copy.deepcopy(draft.current)
    await form(message, actor, draft)


async def form(message, actor, draft, page=None):
    draft.waiting = None
    draft.expires = time.monotonic() + TTL
    if page is not None:
        draft.page = page
    operation = OPERATIONS[draft.key]
    rows = []
    for path, spec in draft.fields[draft.page * PAGE_SIZE:(draft.page + 1) * PAGE_SIZE]:
        if path == ("confirm",):
            # Единственное разрешение даёт отдельная кнопка подтверждения ниже.
            continue
        value = api.get_value(draft.values, path)
        rows.append([(f"{label(path)}: {short(value, path)}", "field", path)])
    navigation = []
    if draft.page:
        navigation.append(("← Поля", "form", draft.page - 1))
    if (draft.page + 1) * PAGE_SIZE < len(draft.fields):
        navigation.append(("Ещё поля →", "form", draft.page + 1))
    if navigation:
        rows.append(navigation)
    if draft.key != "client_access":
        submit_label = "Продолжить →" if needs_confirmation(draft.key) else "Создать" if draft.key in {
            "group_new", "user_new", "node_new", "backup_create", "migration_export"
        } else "Сохранить" if operation.method in {"PUT", "PATCH"} else "Выполнить"
        rows.append([(submit_label, "review", None)])
    rows.append([("Отмена", "section", (operation.section, 0))])
    text = ''
    if draft.key == "client_access":
        text = "Выберите профиль и включите или отключите его. Изменение сохраняется сразу."
    if not draft.fields:
        text = escape(operation.hint) or "Действие не требует дополнительных параметров."
    await draw(message, actor, operation.title, text, rows)


async def edit_field(message, actor, draft, path):
    if path == ("templates",):
        await templates(message, actor, draft)
        return
    spec = dict(draft.fields)[path]
    if path == ("node_ids",):
        await countries(message, actor, draft)
        return
    if path == ("ids",) and draft.key == "node_order":
        await node_order(message, actor, draft)
        return
    if path[-1] in SOURCES:
        await choose(message, actor, draft, path)
        return
    values = spec.get("enum", [True, False] if spec.get("type") == "boolean" else [])
    if values:
        rows = [[(short(v), "value", (path, v))] for v in values]
        rows.append([("← К настройкам", "form", draft.page)])
        await draw(message, actor, label(path), "Выберите значение.", rows)
        return
    draft.waiting = path
    draft.expires = time.monotonic() + TTL
    hint = "Отправьте новое значение. Дефис очищает текстовое поле."
    if spec.get("type") in {"integer", "number"}:
        hint = "Отправьте число."
    if spec.get("type") == "array":
        hint = "Отправьте ID через пробел или запятую." if spec.get("items", {}).get("type") == "integer" else "Отправьте список в формате JSON. Пример структуры доступен в текущем значении."
    if spec.get("type") == "object":
        hint = "Отправьте объект JSON с нужными параметрами."
    if path == ("data",) and draft.key == "restore_preview":
        hint = "Прикрепите зашифрованный файл резервной копии (до 20 МБ)."
    if path == ("html",):
        hint = "Прикрепите HTML-файл заглушки (UTF-8, до 1 МБ)."
    if secret(path):
        hint += "\nЗначение не будет показано в карточке. Обработанное сообщение удаляется из чата, если Telegram разрешает."
    current = api.get_value(draft.values, path)
    await draw(message, actor, label(path), escape(hint) + "\n\nСейчас: " + escape(short(current, path)),
               [[("← К настройкам", "form", draft.page)]])


async def countries(message, actor, draft, page=0):
    nodes = await api.request(actor, message.chat.id, "GET", "/api/v1/nodes")
    selected = api.get_value(draft.values, ("node_ids",), [])
    rows = []
    for node in nodes[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        title = ("✅ " if node["id"] in selected else "⬜ ") + node["name"]
        rows.append([(title, "country_toggle", (node["id"], page))])
    nav = []
    if page:
        nav.append(("← Назад", "countries", page - 1))
    if (page + 1) * PAGE_SIZE < len(nodes):
        nav.append(("Далее →", "countries", page + 1))
    if nav:
        rows.append(nav)
    rows.append([("Готово", "form", draft.page)])
    await draw(message, actor, "Страны в подписке", "Отметьте нужные ноды. Список применится по кнопке «Сохранить» в форме.", rows)


def template_specs():
    schema, root = api.body_schema("templates_edit")
    item = api.resolve(schema["properties"]["templates"]["items"], root)
    return {path[-1]: spec for path, spec in api.fields(item, root)}


async def templates(message, actor, draft, page=0):
    values = draft.values.setdefault("templates", [])
    rows = [[(item["name"], "template_item", i)] for i, item in enumerate(values)
            if page * PAGE_SIZE <= i < (page + 1) * PAGE_SIZE]
    nav = []
    if page:
        nav.append(("← Назад", "templates", page - 1))
    if (page + 1) * PAGE_SIZE < len(values):
        nav.append(("Далее →", "templates", page + 1))
    if nav:
        rows.append(nav)
    if len(values) < 20:
        rows.append([("Добавить шаблон", "template_add", None)])
    rows.append([("Сохранить", "review", None)])
    await draw(message, actor, "Шаблоны подписок", "", rows)


async def template_item(message, actor, draft, index):
    item = draft.values["templates"][index]
    from .subscriptions import traffic_label
    rows = [[(label((key,)) + ": " + (traffic_label(value) if key == "monthly_traffic_limit" else short(value)), "template_field", ("templates", str(index), key))]
            for key, value in item.items() if key != "id"]
    rows.append([("Убрать из списка", "template_remove", index)])
    rows.append([("Сохранить", "review", None), ("← Шаблоны", "templates", 0)])
    await draw(message, actor, item["name"], "", rows)


async def template_field(message, actor, draft, path):
    spec = template_specs()[path[-1]]
    if spec.get("enum"):
        await draw(message, actor, label((path[-1],)), "Выберите значение.",
                   [[(short(value), "value", (path, value))] for value in spec["enum"]])
        return
    draft.waiting = path
    prompt = "Введите месячный лимит в ГБ. 0 — без ограничений." if path[-1] == "monthly_traffic_limit" else "Отправьте новое название шаблона."
    await draw(message, actor, label((path[-1],)), prompt, [[("Отмена", "template_item", int(path[1]))]])


async def node_order(message, actor, draft, page=0):
    nodes = await api.request(actor, message.chat.id, "GET", "/api/v1/nodes")
    names = {node["id"]: node["name"] for node in nodes}
    ids = api.get_value(draft.values, ("ids",), [])
    rows, lines = [], []
    for i in range(page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, len(ids))):
        identifier = ids[i]
        title = names.get(identifier, str(identifier))
        lines.append(f"{i + 1}. {title}")
        moves = []
        if i:
            moves.append(("↑ " + title, "move_node", (i, -1, page)))
        if i + 1 < len(ids):
            moves.append(("↓ " + title, "move_node", (i, 1, page)))
        if moves:
            rows.append(moves)
    nav = []
    if page:
        nav.append(("← Назад", "order_page", page - 1))
    if (page + 1) * PAGE_SIZE < len(ids):
        nav.append(("Далее →", "order_page", page + 1))
    if nav:
        rows.append(nav)
    rows.append([("Готово", "form", draft.page)])
    await draw(message, actor, "Порядок резервирования", escape("\n".join(lines)[:2700]) + "\n\nПеремещайте ноды стрелками, затем сохраните форму.", rows)


async def apply_access_field(message, actor, draft, path, selected):
    fields = [(path, dict(draft.fields)[path])]
    payload = {}
    api.set_value(payload, path, selected)
    single = Draft(draft.key, params=draft.params, fields=fields, values=payload,
                   current=api.initial_values(fields, draft.current))
    await execute(message, actor, single)
    await continue_start(message, actor, draft)


async def review(message, actor, draft):
    missing = object()
    for path, spec in draft.fields:
        if path == ("confirm",):
            continue
        value = api.get_value(draft.values, path, missing)
        if spec.get("required") and (value is missing or value is None and not spec.get("nullable")):
            raise ValueError("Заполните поле «" + label(path) + "».")
    if not needs_confirmation(draft.key):
        cancel(actor, message.chat.id)
        await safe_execute(message, actor, draft)
        return
    operation = OPERATIONS[draft.key]
    changed = [(path, value) for path, _ in draft.fields if path != ("confirm",)
               and (value := api.get_value(draft.values, path)) is not None
               and (not operation.current or value != api.get_value(draft.current,path))]
    lines = [f"{label(path)}: {short(value, path)}" for path, value in changed]
    lines += [f"{label((key,))}: {value}" for key, value in draft.params.items()]
    # При длинной форме не скрываем параметры: полная карточка остаётся доступна
    # кнопкой «Изменить», а итог показывает только изменённые поля.
    if sum(map(len, lines)) > 2300:
        lines = [f"{label(path)}: {short(value, path)}" for path, value in changed
                 if value != api.get_value(draft.current, path)]
    text = escaped_text(draft.context or "\n".join(lines[:4]), 800)
    if draft.key in {'restore','migration_start','update_install','client_delete','client_sub','profile_regenerate'}:
        text += '\n'+escaped_text(operation.hint,250)
    text += "\n\nПрименить?"
    await draw(message, actor, operation.title, text,
               [[("✅ Подтвердить", "confirm", copy.deepcopy(draft))], [("Изменить", "form", draft.page)],
                [("Отмена", "section", (operation.section, 0))]])


async def execute(message, actor, draft):
    operation = OPERATIONS[draft.key]
    if message.chat.id in running:
        raise ValueError("Предыдущее действие ещё выполняется.")
    running.add(message.chat.id)
    try:
        if draft.node_state is not None:
            identifier = draft.values.get('node_id') if draft.key == 'active_node' else draft.params['node_id']
            current_node = await api.call(actor, message.chat.id, 'node', {'node_id': identifier})
            if (current_node.get('name'), current_node.get('host'), current_node.get('is_enabled', True)) != draft.node_state:
                raise ValueError('Нода уже изменилась. Обновите список.')
        payload = copy.deepcopy(draft.values) if draft.fields else None
        if draft.fields and ("confirm",) in dict(draft.fields):
            payload["confirm"] = True
        if operation.current and draft.key != "placeholder_edit":
            current = await api.current_values(actor, message.chat.id, draft.key, draft.params)
            if api.initial_values(draft.fields, current) != draft.current:
                raise ValueError("Настройки уже изменились. Откройте форму заново, чтобы не перезаписать изменения.")
        await draw(message, actor, operation.title, "⏳ Выполняю…", [])
        value = await api.call(actor, message.chat.id, draft.key, draft.params, payload)
        if draft.node_page is not None:
            await nodes_list(message, actor, draft.node_page)
            return
        if draft.key in {"telegram", "restore"}:
            from .bot import get_bot
            active_bot = get_bot()
            if active_bot:
                message = message.as_(active_bot)
            try:
                await api.authorize(actor, message.chat.id)
            except ValueError:
                return
        if draft.key == "restore_preview" and isinstance(value, dict) and value.get("token"):
            tables=value.get('tables',[])
            summary=' · '.join(f'{title}: {sum(int(item.get(field,0)) for item in tables)}' for title,field in
                             (('Добавится','added'),('Изменится','changed'),('Удалится','removed')))
            await draw(message, actor, "Проверка резервной копии",
                       escaped_text(summary)+'\n\n'+escaped_text(value.get('notice','Данные панели будут заменены.'),700),
                       [[("Перейти к восстановлению", "restore_ready", {
                           "token": value["token"], "password": payload["password"]})],
                        [("Отмена", "section", ("backups", 0))]])
            return
        if draft.return_view is not None and not isinstance(value,bytes):
            from .admin_compact import open_view
            await open_view(sys_module(),message,actor,*draft.return_view)
            return
        await result_screen(message, actor, draft.key, value)
    finally:
        running.discard(message.chat.id)


async def safe_execute(message, actor, draft):
    try:
        await execute(message, actor, draft)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Операция владельца Telegram %s: %s", draft.key, type(exc).__name__)
        # Никаких traceback/параметров запроса в Telegram или журнале.
        text = str(exc) if isinstance(exc, ValueError) else "Операция не завершена. Проверьте состояние и журнал перед повтором."
        # Ошибки API могут включать введённое значение; секреты убираем и здесь.
        for path, _ in draft.fields:
            value = api.get_value(draft.values, path)
            if secret(path) and isinstance(value, str) and value:
                text = text.replace(value, "[скрыто]")
        await draw(message, actor, "Не удалось выполнить действие", escaped_text(text, 2500),
                   [[("← В раздел", "section", (OPERATIONS[draft.key].section, 0))]])


async def invite_command(message, actor, token):
    origin = os.getenv("PANEL_PUBLIC_URL", "").rstrip("/")
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Не задан HTTPS-адрес панели (PANEL_PUBLIC_URL). Откройте приглашение в панели.")
    command = "curl -fsSL " + shlex.quote(origin + "/api/v1/nodes/join?" + urlencode({"token": token})) + " | bash"
    await draw(message, actor, "Установка ноды", "Выполните на сервере новой ноды:\n\n<code>" + escape(command) + "</code>",
               [[("← Ноды", "section", ("nodes", 0))]])


async def callback(event):
    message, actor = event.message, event.from_user.id
    await api.authorize(actor, message.chat.id)
    await event.answer()
    clean()
    if event.data == "adm:home":
        await home(message, actor)
        return
    if event.data == 'adm:nodes':
        cancel(actor, message.chat.id)
        try:
            await nodes_list(message, actor)
        except Exception as exc:
            logger.warning('Список нод Telegram: %s', type(exc).__name__)
            await draw(message, actor, 'Ноды', 'Не удалось загрузить. Попробуйте ещё раз.',
                       [[('Обновить', 'nodes_list', 0)]])
        return
    token = event.data.removeprefix("adm:b:")
    ticket = buttons.get(token)
    if not ticket or (ticket["actor"], ticket["chat"], ticket["message"]) != (actor, message.chat.id, message.message_id):
        await draw(message, actor, "Кнопка устарела", "Откройте раздел заново. Подтверждения действуют ограниченное время.", [[("Все разделы", "home", None)]])
        return
    action, value = ticket["action"], ticket["value"]
    # До первого await: повторный клик не выполнит действие или переключатель
    # второй раз, пока Telegram ещё показывает предыдущую клавиатуру.
    buttons.pop(token, None)
    try:
        draft = drafts.get((actor, message.chat.id))
        if action == "home":
            await home(message, actor)
        elif action == "section":
            await section(message, actor, *value)
        elif action == "start":
            await start(message, actor, value)
        elif action == 'nodes_list':
            cancel(actor, message.chat.id)
            await nodes_list(message, actor, value)
        elif action == 'nodes_settings':
            await nodes_settings(message, actor, value)
        elif action == 'compact_group':
            from .admin_compact import menu
            cancel(actor,message.chat.id)
            await menu(sys_module(),message,actor,value)
        elif action == 'compact_page':
            from .admin_compact import open_view
            cancel(actor,message.chat.id)
            await open_view(sys_module(),message,actor,*value)
        elif action == 'compact_bound':
            from .admin_compact import bound_action
            await bound_action(sys_module(),message,actor,value)
        elif action == 'compact_template':
            from .admin_compact import edit_template
            await edit_template(sys_module(),message,actor,value)
        elif action == 'node_action':
            await node_action(message, actor, *value)
        elif action == "result":
            await result_screen(message, actor, *value)
        elif action == "invite_command":
            await invite_command(message, actor, value)
        elif action == "totp_key":
            await draw(message, actor, "Ключ двухфакторной защиты", "Добавьте ключ в приложение-аутентификатор, затем подтвердите код:\n\n<code>" + escape(value) + "</code>",
                       [[("Подтвердить код", "start", "totp_verify")], [("← Безопасность", "section", ("security", 0))]])
        elif action == "restore_ready":
            schema, root = api.body_schema("restore")
            draft = Draft("restore", fields=api.fields(schema, root), values=value)
            drafts[(actor, message.chat.id)] = draft
            await review(message, actor, draft)
        elif action == "confirm":
            cancel(actor, message.chat.id)
            if value.key in {"telegram", "restore"}:
                # Эти API перезапускают polling. Задача живёт вне обработчика
                # Dispatcher, поэтому stop_polling не ждёт сам себя.
                task = asyncio.create_task(safe_execute(message, actor, value))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            else:
                await safe_execute(message, actor, value)
        else:
            if draft is None:
                raise ValueError("Форма устарела. Откройте действие снова.")
            draft.expires = time.monotonic() + TTL
            if action == "param":
                path, identifier = value
                draft.params[path[0]] = identifier
                await continue_start(message, actor, draft)
            elif action == "choose":
                path, page, parameter = value
                await choose(message, actor, draft, path, page, parameter)
            elif action == "field":
                if draft.key == "client_access":
                    await apply_access_field(message, actor, draft, value, not api.get_value(draft.values, value))
                else:
                    await edit_field(message, actor, draft, value)
            elif action == "templates":
                draft.waiting = None
                await templates(message, actor, draft, value)
            elif action == "template_item":
                draft.waiting = None
                await template_item(message, actor, draft, value)
            elif action == "template_add":
                items = draft.values.setdefault("templates", [])
                if len(items) >= 20:
                    raise ValueError("Не больше 20 шаблонов.")
                items.append({"id": secrets.token_hex(6), "name": "Новый шаблон", "period": "month", "monthly_traffic_limit": 0})
                await template_item(message, actor, draft, len(items) - 1)
            elif action == "template_remove":
                draft.values["templates"].pop(value)
                await templates(message, actor, draft)
            elif action == "template_field":
                await template_field(message, actor, draft, value)
            elif action == "countries":
                await countries(message, actor, draft, value)
            elif action == "country_toggle":
                identifier, page = value
                selected = draft.values.setdefault("node_ids", [])
                if identifier in selected:
                    selected.remove(identifier)
                else:
                    selected.append(identifier)
                await countries(message, actor, draft, page)
            elif action == "move_node":
                index, offset, page = value
                ids = draft.values["ids"]
                other = index + offset
                if 0 <= index < len(ids) and 0 <= other < len(ids):
                    ids[index], ids[other] = ids[other], ids[index]
                await node_order(message, actor, draft, page)
            elif action == "order_page":
                await node_order(message, actor, draft, value)
            elif action == "value":
                path, selected = value
                api.set_value(draft.values, path, selected)
                if path[0] == "templates" and len(path) == 3:
                    await template_item(message, actor, draft, int(path[1]))
                elif draft.key == "client_access":
                    await apply_access_field(message, actor, draft, path, selected)
                else:
                    await form(message, actor, draft)
            elif action == "form":
                await form(message, actor, draft, value)
            elif action == "review":
                await review(message, actor, draft)
    except (ValueError, KeyError) as exc:
        text = str(exc) if isinstance(exc, ValueError) else "Кнопка устарела. Откройте раздел снова."
        draft = drafts.get((actor, message.chat.id))
        await draw(message, actor, "Проверьте данные", escape(text[:1800]),
                   [[("← К форме", "form", draft.page)]] if draft and draft.fields else [[("Все разделы", "home", None)]])
    except Exception as exc:
        logger.warning("Экран владельца Telegram: %s", type(exc).__name__)
        await draw(message, actor, "Не удалось загрузить данные", "Проверьте состояние панели и повторите запрос.", [[("Все разделы", "home", None)]])


async def text_input(message):
    actor, chat = message.from_user.id, message.chat.id
    await api.authorize(actor, chat)
    clean()
    draft = drafts.get((actor, chat))
    if not draft or draft.waiting is None:
        return
    path = draft.waiting
    try:
        if message.document:
            html_file = path == ("html",) and draft.key == "placeholder_edit"
            if not (html_file or path == ("data",) and draft.key == "restore_preview"):
                raise ValueError("Это поле принимает текст.")
            limit = 1024 * 1024 if html_file else api.MAX_FILE_SIZE
            if not message.document.file_size or message.document.file_size > limit:
                raise ValueError("Файл превышает допустимый размер: " + ("1 МБ." if html_file else "20 МБ."))
            data = await message.bot.download(message.document, timeout=60)
            raw = data.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Файл слишком большой.")
            try:
                value = raw.decode("utf-8") if html_file else base64.b64encode(raw).decode()
            except UnicodeDecodeError:
                raise ValueError("HTML-файл должен быть в кодировке UTF-8.") from None
        else:
            if path[0] == "templates" and len(path) == 3:
                if path[-1] == "monthly_traffic_limit":
                    from decimal import Decimal, InvalidOperation
                    try:
                        amount = Decimal((message.text or "").replace(",", "."))
                        if not amount.is_finite() or not 0 <= amount <= 8388607:
                            raise ValueError("Неверный лимит в ГБ.")
                        value = int(amount * 1024 ** 3)
                    except InvalidOperation:
                        raise ValueError("Введите лимит числом, например 100 или 25,5.") from None
                else:
                    value = api.parse_value(message.text or "", template_specs()[path[-1]])
            else:
                value = api.parse_value(message.text or "", dict(draft.fields)[path])
        api.set_value(draft.values, path, value)
        if path[0] == "templates" and len(path) == 3:
            draft.waiting = None
            await template_item(message, actor, draft, int(path[1]))
        else:
            await form(message, actor, draft)
    except ValueError as exc:
        await draw(message, actor, "Проверьте значение", escape(str(exc)), [[("← К форме", "form", draft.page)]])


def register(router):
    router.callback_query.register(callback, F.data.startswith("adm:"))
    router.message.register(text_input, (F.text | F.document) & F.func(
        lambda m: bool(m.from_user and (draft := drafts.get((m.from_user.id, m.chat.id))) and draft.waiting is not None)))
