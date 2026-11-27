"""Сценарии владельца без запросов к Telegram и без изменения сервисов."""
import copy
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat, Message, User

from app.bot import admin_api, admin_console as console, handlers, inbox
from app.bot.admin_catalog import OPERATIONS
from app.db.database import AsyncSessionLocal
from app.models.client import ClientGroup


def message(identifier=10, actor=42):
    return Message(message_id=identifier, date=datetime.now(timezone.utc),
                   chat=Chat(id=actor, type="private"),
                   from_user=User(id=123, is_bot=True, first_name="Бот"), text="Меню")


def callback(data, identifier=10, actor=42):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=actor),
                           message=message(identifier, actor), answer=AsyncMock())


@pytest.fixture(autouse=True)
def isolated_console():
    console.buttons.clear()
    console.drafts.clear()
    console.running.clear()
    with patch.object(admin_api, "get_telegram_settings_from_db", AsyncMock(return_value={"admin_id": 42})):
        yield
    console.buttons.clear()
    console.drafts.clear()
    console.running.clear()


def ticket(action, value, **extra):
    console.buttons["ticket"] = {"actor": 42, "chat": 42, "message": 10,
        "expires": time.monotonic() + 60, "action": action, "value": value, **extra}
    return callback("adm:b:ticket")


def test_catalog_matches_actual_api_and_has_no_login_or_node_agent_credentials():
    from app.main import app
    schema = app.openapi()
    for operation in OPERATIONS.values():
        assert operation.method.lower() in schema["paths"][operation.path]
        assert "/login" not in operation.path
        assert not operation.path.endswith("/register") or operation.key == "warp_register"
        admin_api.body_schema(operation.key)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,chat", [(7, 7), (42, -100), (0, 0)])
async def test_non_owner_or_non_private_chat_never_reaches_api(actor, chat):
    with pytest.raises(ValueError, match="владельцу"):
        await admin_api.call(actor, chat, "group_new", payload={"name": "чужая"})
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(ClientGroup.__table__.select())).all()


@pytest.mark.asyncio
async def test_bridge_uses_real_api_validation_and_owner_identity():
    with pytest.raises(ValueError, match="HTTP 422"):
        await admin_api.call(42, 42, "group_new", payload={"name": ""})
    group = await admin_api.call(42, 42, "group_new", payload={"name": "Тестовая", "color": "blue"})
    result = await admin_api.call(42, 42, "groups")
    assert result["groups"][0]["id"] == group["id"]
    await admin_api.call(42, 42, "group_delete", {"group_id": group["id"]})
    assert (await admin_api.call(42, 42, "groups"))["groups"] == []


@pytest.mark.asyncio
async def test_creation_form_does_not_apply_until_create_button():
    with patch.object(console, "draw", AsyncMock()), patch.object(console, "safe_execute", AsyncMock()) as apply:
        await console.start(message(), 42, "group_new")
        draft = console.drafts[(42, 42)]
        draft.values["name"] = "Новая"
        apply.assert_not_awaited()
        await console.review(message(), 42, draft)
        apply.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_runs_once_and_is_consumed_before_await():
    draft = console.Draft("group_delete", params={"group_id": 7})
    event = ticket("confirm", draft)
    with patch.object(console, "safe_execute", AsyncMock()) as apply, patch.object(console, "draw", AsyncMock()):
        await console.callback(event)
        await console.callback(event)
        apply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"message": 99}, {"chat": 99}, {"actor": 99}, {"expires": 0}])
async def test_stale_or_foreign_confirmation_is_rejected(extra):
    event = ticket("confirm", console.Draft("group_delete", params={"group_id": 7}), **extra)
    with patch.object(console, "safe_execute", AsyncMock()) as apply, patch.object(console, "draw", AsyncMock()):
        await console.callback(event)
        apply.assert_not_awaited()


def test_navigation_invalidates_old_confirmations():
    ticket("confirm", console.Draft("warp_disconnect"))
    console.invalidate(42, 10, handlers.keyboard([("Меню", "md:home")]))
    assert not console.buttons


@pytest.mark.asyncio
async def test_changed_settings_cannot_be_overwritten_from_old_form():
    schema, root = admin_api.body_schema("dns_edit")
    fields = admin_api.fields(schema, root)
    draft = console.Draft("dns_edit", fields=fields, current=admin_api.initial_values(fields, {}),
                          values=admin_api.initial_values(fields, {}))
    current = {**draft.current, "remote_ip": "9.9.9.9"}
    with patch.object(admin_api, "current_values", AsyncMock(return_value=current)), \
         patch.object(admin_api, "call", AsyncMock()) as apply:
        with pytest.raises(ValueError, match="изменились"):
            await console.execute(message(), 42, draft)
        apply.assert_not_awaited()
    assert not console.running


@pytest.mark.asyncio
async def test_unavailable_current_settings_do_not_create_reset_form():
    with patch.object(admin_api, "current_values", AsyncMock(side_effect=ValueError("Недоступно"))), \
         patch.object(console, "form", AsyncMock()) as form:
        with pytest.raises(ValueError):
            await console.start(message(), 42, "dns_edit")
        form.assert_not_awaited()


def test_protocol_fields_are_expanded_and_preserve_existing_values():
    schema, root = admin_api.body_schema("protocols_edit")
    current = admin_api.normalize_current("protocols_edit", {
        "profiles": [{"kind": "awg", "enabled": True}, {"kind": "mtproto", "enabled": False}],
        "ports": {"mtproto": 8445}, "paths": {"vless_xhttp_tls": "/vpn"},
    }, {})
    fields = admin_api.fields(schema, root, current)
    values = admin_api.initial_values(fields, current)
    assert values["enabled"] == {"awg": True, "mtproto": False}
    assert values["ports"]["mtproto"] == 8445
    assert ("enabled", "awg") in dict(fields)
    assert "profiles" not in values


@pytest.mark.parametrize("text,spec,expected", [
    ("да", {"type": "boolean"}, True), ("4", {"type": "integer", "minimum": 1}, 4),
    ("1, 3 7", {"type": "array", "items": {"type": "integer"}}, [1, 3, 7]),
    ("—", {"nullable": True}, None), ("-", {"type": "string"}, ""),
])
def test_typed_values(text, spec, expected):
    assert admin_api.parse_value(text, spec) == expected


@pytest.mark.parametrize("text,spec", [
    ("maybe", {"type": "boolean"}), ("0", {"type": "integer", "minimum": 1}),
    ("other", {"enum": ["manual", "auto"]}), ("{}", {"type": "array"}),
])
def test_invalid_values_are_rejected(text, spec):
    with pytest.raises(ValueError):
        admin_api.parse_value(text, spec)


def test_secrets_are_hidden_in_results_and_confirmation():
    lines = console.readable({"password": "password-value", "nested": {"private_key": "key-value"},
                             "old_password": "old-value", "proxy_url": "socks5://user:pass@host"})
    assert all(value not in "\n".join(lines) for value in ("password-value", "key-value", "old-value", "user:pass"))


@pytest.mark.asyncio
async def test_error_response_redacts_input_secrets():
    draft = console.Draft("backup_create", fields=[(("password",), {})], values={"password": "sensitive-value"})
    with patch.object(console, "execute", AsyncMock(side_effect=ValueError("Ошибка sensitive-value"))), \
         patch.object(console, "draw", AsyncMock()) as draw:
        await console.safe_execute(message(), 42, draft)
        assert "sensitive-value" not in draw.call_args.args[3]


def test_notifications_wait_during_forms_and_resume_after_cancel():
    console.drafts[(42, 42)] = console.Draft("node_new")
    assert inbox.editing(42)
    console.cancel(42, 42)
    assert not console.editing(42)


def test_expired_input_and_secrets_are_removed():
    console.drafts[(42, 42)] = console.Draft("backup_create", values={"password": "value"}, expires=0)
    console.clean()
    assert not console.drafts


@pytest.mark.asyncio
async def test_restore_uses_preview_token_and_requires_second_confirmation():
    event = ticket("restore_ready", {"token": "preview-token", "password": "backup-password"})
    with patch.object(console, "review", AsyncMock()) as review, patch.object(admin_api, "call", AsyncMock()) as apply:
        await console.callback(event)
        draft = review.call_args.args[2]
        assert draft.key == "restore" and draft.values["token"] == "preview-token"
        apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_nullable_required_field_can_select_common_route():
    schema, root = admin_api.body_schema("active_node")
    draft = console.Draft("active_node", fields=admin_api.fields(schema, root), values={"node_id": None})
    with patch.object(console, "draw", AsyncMock()) as draw:
        await console.review(message(), 42, draft)
        assert draw.await_count == 1


@pytest.mark.asyncio
async def test_client_profiles_apply_immediately_without_review():
    fields = [(("profiles", "awg"), {"type": "boolean"})]
    draft = console.Draft("client_access", params={"client_id": 9}, fields=fields,
                          values={"profiles": {"awg": True}}, current={"profiles": {"awg": True}})
    console.drafts[(42, 42)] = draft
    event = ticket("value", (("profiles", "awg"), False))
    with patch.object(console, "execute", AsyncMock()) as apply, \
         patch.object(console, "continue_start", AsyncMock()), patch.object(console, "review", AsyncMock()) as review:
        await console.callback(event)
        assert apply.call_args.args[2].values == {"profiles": {"awg": False}}
        review.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_field_is_one_click_toggle():
    fields = [(("profiles", "awg"), {"type": "boolean"})]
    draft = console.Draft("client_access", params={"client_id": 9}, fields=fields,
                          values={"profiles": {"awg": True}}, current={"profiles": {"awg": True}})
    console.drafts[(42, 42)] = draft
    event = ticket("field", ("profiles", "awg"))
    with patch.object(console, "apply_access_field", AsyncMock()) as apply, patch.object(console, "edit_field", AsyncMock()) as ask:
        await console.callback(event)
        assert apply.call_args.args[-1] is False
        ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_confirmation_supplies_required_confirm_field():
    schema, root = admin_api.body_schema("restore")
    draft = console.Draft("restore", fields=admin_api.fields(schema, root), values={"token": "preview", "password": "backup-password"})
    with patch.object(console, "draw", AsyncMock()) as draw:
        await console.review(message(), 42, draft)
        assert draw.call_args.args[4][0][0][1] == "confirm"


def test_protocol_metadata_is_not_sent_back_as_a_setting():
    current = admin_api.normalize_current("protocols_edit", {
        "profiles": [{"kind": "awg", "enabled": True}],
        "reality": {"private_key_set": True, "public_key": "public"},
    }, {})
    schema, root = admin_api.body_schema("protocols_edit")
    fields = admin_api.fields(schema, root, current)
    values = admin_api.initial_values(fields, current)
    assert "private_key_set" not in values["reality"]
    assert "private_key" not in values["reality"]
    assert ("reality", "private_key") in dict(fields)


def test_template_fields_work_without_json_input():
    values = {"templates": [{"id": "month", "name": "Месяц"}]}
    admin_api.set_value(values, ("templates", "0", "name"), "Новый")
    assert admin_api.get_value(values, ("templates", "0", "name")) == "Новый"
    assert {"id", "name", "period", "monthly_traffic_limit"} == set(console.template_specs())


def test_long_html_values_do_not_break_telegram_limits_or_entities():
    text = console.escaped_text('<&"' * 3000)
    assert len(text) < 3100
    assert "<" not in text
    assert text.endswith("…")
    import re
    assert '&' not in re.sub(r'&(?:amp|lt|quot);', '', text)


@pytest.mark.asyncio
async def test_large_download_stops_before_buffering_entire_file(monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from app import main as main_module
    application = FastAPI()
    chunks_sent = []
    async def chunks():
        for index in range(10):
            chunks_sent.append(index)
            yield b'x' * 16
    @application.get('/download')
    async def download():
        return StreamingResponse(chunks())
    monkeypatch.setattr(main_module, 'app', application)
    monkeypatch.setattr(admin_api, 'MAX_FILE_SIZE', 20)
    with pytest.raises(ValueError, match='20 МБ'):
        await admin_api.request(42, 42, 'GET', '/download')
    assert len(chunks_sent) < 10


@pytest.mark.asyncio
async def test_settings_reload_runs_outside_dispatcher_handler():
    event = ticket("confirm", console.Draft("telegram"))
    with patch.object(console, "safe_execute", AsyncMock()) as execute:
        await console.callback(event)
        await __import__('asyncio').gather(*list(console.tasks))
        execute.assert_awaited_once()


@pytest.mark.parametrize("value", ["../file", "a/b", "a\\b", ""])
def test_paths_cannot_escape_catalog(value):
    with pytest.raises(ValueError):
        admin_api.endpoint(OPERATIONS["backup_download"], {"name": value})
