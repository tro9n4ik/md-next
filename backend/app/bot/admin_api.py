"""Внутренний доступ бота к тем же API и проверкам, что использует панель."""
import asyncio
import copy
import json
import re
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.user import User
from app.services.telegram_settings import get_telegram_settings_from_db
from .admin_catalog import OPERATIONS

MAX_FILE_SIZE = 20 * 1024 * 1024


async def authorize(actor, chat):
    settings = await get_telegram_settings_from_db()
    if not actor or actor != chat or actor != settings.get("admin_id"):
        raise ValueError("Управление доступно только владельцу в личном диалоге.")


async def request(actor, chat, method, path, payload=None):
    await authorize(actor, chat)
    from app.api.auth import create_access_token
    from app.main import app
    async with AsyncSessionLocal() as db:
        owner = (await db.execute(select(User).where(
            User.username == "admin", User.role == "admin", User.is_active.is_(True)
        ))).scalar_one_or_none()
        if owner is None:
            raise ValueError("Активная учётная запись владельца не найдена.")
        token = create_access_token({"sub": owner.username, "token_version": owner.token_version or 1})

    # Ограничиваем размер до накопления ответа ASGI в памяти (комплект переезда
    # может быть значительно больше предела Telegram).
    async def bounded_app(scope, receive, send):
        total = 0
        async def bounded_send(message):
            nonlocal total
            if message["type"] == "http.response.body":
                total += len(message.get("body", b""))
                if total > MAX_FILE_SIZE:
                    raise ValueError("Файл больше 20 МБ. Скачайте его через панель.")
            await send(message)
        await app(scope, receive, bounded_send)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bounded_app),
                                 base_url="http://telegram.internal") as client:
        try:
            async with asyncio.timeout(180):
                arguments = {"content": payload} if isinstance(payload, bytes) else {"json": payload}
                response = await client.request(method, path, **arguments,
                    headers={"Authorization": "Bearer " + token})
        except TimeoutError:
            raise ValueError("Ответ не получен вовремя. Проверьте состояние операции перед повтором.") from None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            detail = ""
        if isinstance(detail, list):
            # Не возвращаем Pydantic input/ctx: в них бывают пароли и ключи.
            detail = "; ".join(" / ".join(map(str, item.get("loc", [])[1:])) + ": " + item.get("msg", "Ошибка ввода")
                               for item in detail[:4])
        if not isinstance(detail, str):
            detail = detail.get("message", "") if isinstance(detail, dict) else ""
        raise ValueError(f"HTTP {response.status_code}: {detail[:1000] or 'Операция не выполнена. Проверьте журнал событий.'}")
    if response.status_code == 204:
        return {"message": "Готово"}
    if "application/json" in response.headers.get("content-type", ""):
        return response.json()
    return response.content


def endpoint(operation, params):
    path = operation.path
    for name in re.findall(r"{(\w+)}", path):
        value = str(params[name])
        if not value or "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Некорректный идентификатор.")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    return path


async def call(actor, chat, key, params=None, payload=None):
    operation = OPERATIONS[key]
    if key == "placeholder_edit":
        payload = payload["html"].encode("utf-8")
    return await request(actor, chat, operation.method, endpoint(operation, params or {}), payload)


def resolve(schema, root):
    schema = copy.deepcopy(schema)
    if "$ref" in schema:
        target = root
        for part in schema.pop("$ref").removeprefix("#/").split("/"):
            target = target[part]
        schema = {**copy.deepcopy(target), **schema}
    if "anyOf" in schema:
        choices = schema.pop("anyOf")
        schema = {**resolve(next((s for s in choices if s.get("type") != "null"), {}), root), **schema,
                  "nullable": any(s.get("type") == "null" for s in choices)}
    return schema


def body_schema(key):
    from app.main import app
    operation = OPERATIONS[key]
    root = app.openapi()
    route = root["paths"][operation.path][operation.method.lower()]
    body = route.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
    if key == "placeholder_edit":
        body = {"type": "object", "required": ["html"], "properties": {"html": {"type": "string", "maxLength": 1024 * 1024}}}
    return resolve(body, root), root


def fields(schema, root, current=None, prefix=()):
    """Известные вложенные настройки редактируются по полям, а не целым JSON."""
    result = []
    for name, raw in schema.get("properties", {}).items():
        spec = resolve(raw, root)
        value = current.get(name) if isinstance(current, dict) else None
        if spec.get("type") == "object" and not spec.get("properties") and isinstance(value, dict) and value:
            spec["properties"] = {k: {"type": "boolean" if isinstance(v, bool) else "integer" if isinstance(v, int) else "string"}
                                  for k, v in value.items()}
            if prefix == () and name == "reality":
                spec["properties"]["private_key"] = {"type": "string"}
        path = (*prefix, name)
        if spec.get("properties"):
            result.extend(fields(spec, root, value, path))
        else:
            result.append((path, {**spec, "required": name in schema.get("required", [])}))
    return result


def get_value(data, path, default=None):
    for part in path:
        if isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError, TypeError):
                return default
            continue
        if not isinstance(data, dict) or part not in data:
            return default
        data = data[part]
    return data


def set_value(data, path, value):
    for part in path[:-1]:
        data = data[int(part)] if isinstance(data, list) else data.setdefault(part, {})
    data[int(path[-1]) if isinstance(data, list) else path[-1]] = value


def initial_values(field_list, current):
    missing = object()
    result = {}
    for path, spec in field_list:
        value = get_value(current, path, missing)
        if value is missing:
            value = spec.get("default", missing)
        if value is not missing:
            set_value(result, path, copy.deepcopy(value))
    return result


def normalize_current(key, value, params):
    if key == "node_order":
        return {"ids": [node["id"] for node in sorted(value, key=lambda node: (node.get("priority", 0), node["id"]))]}
    if key == "client_access":
        return {"adblock_enabled": value["adblock_enabled"], "profiles": {
            p["kind"]: p["is_enabled"] for p in value["access"] if p["available"]}}
    if key == "templates_edit":
        return {"templates": value}
    if key in {"group_edit", "user_edit"}:
        collection = value.get("groups", []) if isinstance(value, dict) else value
        identifier = params.get("group_id", params.get("user_id"))
        value = next((v for v in collection if str(v.get("id")) == str(identifier)), None)
        if value is None:
            raise ValueError("Объект больше не существует.")
    if key == "protocols_edit":
        value = {**value, "enabled": {p["kind"]: p["enabled"] for p in value["profiles"]},
                 "reality": {k: v for k, v in value.get("reality", {}).items() if k != "private_key_set"}}
    if key == "client_exit":
        route = value.get("route", {})
        value = {**value, **route}
    return value


async def current_values(actor, chat, key, params):
    operation = OPERATIONS[key]
    if not operation.current:
        return {}
    # current — фиксированный путь каталога; имена параметров совпадают с операцией.
    current_op = type(operation)(operation.key, operation.section, operation.title, "GET", operation.current)
    value = await request(actor, chat, "GET", endpoint(current_op, params))
    return normalize_current(key, value, params)


def parse_value(text, spec):
    if len(text) > 16000:
        raise ValueError("Значение слишком длинное.")
    if text.strip() == "—" and spec.get("nullable"):
        return None
    kind = spec.get("type", "string")
    try:
        if kind == "boolean":
            values = {"да": True, "нет": False, "true": True, "false": False, "1": True, "0": False}
            value = values[text.strip().lower()]
        elif kind == "integer":
            value = int(text.strip())
        elif kind == "number":
            value = float(text.strip().replace(",", "."))
        elif kind == "array" and spec.get("items", {}).get("type") == "integer":
            value = [int(v) for v in re.split(r"[,\s]+", text.strip()) if v]
        elif kind in {"array", "object"}:
            value = json.loads(text)
            if not isinstance(value, list if kind == "array" else dict):
                raise ValueError()
        else:
            value = "" if text == "-" else text.strip()
    except (ValueError, KeyError):
        raise ValueError("Неверный формат. Проверьте подсказку и отправьте значение снова.") from None
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("Выберите один из предложенных вариантов.")
    if isinstance(value, str) and (len(value) < spec.get("minLength", 0) or len(value) > spec.get("maxLength", 16000)):
        raise ValueError("Длина значения не соответствует ограничению поля.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < spec.get("minimum", float("-inf")) or value > spec.get("maximum", float("inf")):
            raise ValueError("Число вне допустимого диапазона.")
    return value
