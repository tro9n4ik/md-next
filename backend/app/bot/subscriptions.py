"""Подписки администратора: мастер, список и выдача доступа из инлайн-меню."""
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from sqlalchemy import select

from app.db.database import AsyncSessionLocal
from app.models.client import Client
from app.services.client_limits import expiry_for_period, limit_info

TTL = 900
PERIODS = {"week": "Неделя", "month": "Месяц", "year": "Год", "unlimited": "Без срока", "custom": "Своя дата"}
STEPS = ("name", "phone", "email", "period", "quota", "review")


@dataclass
class Draft:
    actor: int
    chat: int
    message: int
    token: str = field(default_factory=lambda: secrets.token_hex(6))
    step: str = "name"
    values: dict = field(default_factory=dict)
    expires: float = field(default_factory=lambda: time.monotonic() + TTL)
    # Каждая кнопка привязана ещё и к версии экрана: старый «Пропустить» не
    # должен случайно пропустить следующий шаг при двойном нажатии.
    revision: int = 0


drafts: dict[tuple[int, int], Draft] = {}


def clean_drafts():
    for key, draft in list(drafts.items()):
        if draft.expires <= time.monotonic():
            drafts.pop(key, None)


def get_draft(actor, chat):
    clean_drafts()
    return drafts.get((actor, chat))


def validate_text(step, value):
    value = value.strip()
    if step == "name":
        if not 1 <= len(value) <= 64:
            raise ValueError("Имя должно содержать от 1 до 64 символов.")
    elif step == "phone":
        if len(value) > 32 or not any(c.isdigit() for c in value) or any(c not in "+0123456789 ()-." for c in value):
            raise ValueError("Введите номер телефона, например +7 999 123-45-67, или нажмите «Пропустить».")
    elif step == "email":
        parts = value.split("@")
        if len(value) > 254 or len(parts) != 2 or not parts[0] or "." not in parts[1] or any(c.isspace() for c in value):
            raise ValueError("Введите почту, например name@example.com, или нажмите «Пропустить».")
    elif step == "date":
        try:
            value = datetime.strptime(value, "%d.%m.%Y").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            expiry_for_period("custom", value, datetime.now(timezone.utc))
        except ValueError:
            raise ValueError("Введите будущую дату в формате ДД.ММ.ГГГГ.") from None
    elif step == "quota_custom":
        try:
            number = Decimal(value.replace(",", "."))
            if not number.is_finite() or not Decimal("0") < number <= Decimal("8388607"):
                raise ValueError
            value = int(number * 1024 ** 3)
            if value < 1:
                raise ValueError
        except (InvalidOperation, ValueError, OverflowError):
            raise ValueError("Введите положительный объём в ГБ, например 100 или 25,5. Для бесконечного трафика используйте кнопку.") from None
    return value


def date_label(value):
    return value.strftime("%d.%m.%Y") if value else "Без срока"


def traffic_label(value):
    return f"{value / 1024 ** 3:g} ГБ" if value else "Без ограничений"


def review_text(draft):
    values = draft.values
    expiry = expiry_for_period(values["subscription_period"], values.get("expires_at"), datetime.now(timezone.utc))
    return ("<b>✅ Проверьте подписку</b>\n\n"
            f"Имя: <b>{escape(values['name'])}</b>\n"
            f"Телефон: {escape(values.get('phone') or 'Не указан')}\n"
            f"Почта: {escape(values.get('email') or 'Не указана')}\n"
            f"Срок: <b>{PERIODS[values['subscription_period']]}</b>\n"
            f"Окончание: <b>{date_label(expiry)}</b>\n"
            f"Трафик в месяц: <b>{traffic_label(values['monthly_traffic_limit'])}</b>\n\n"
            "Месячный период начинается с даты создания подписки. Доступ и профили появятся после подтверждения.")


async def render(draft, message, *, edit=True, error=None):
    from .handlers import keyboard, show_screen
    draft.revision += 1
    draft.expires = time.monotonic() + TTL
    prefix = f"sub:w:{draft.token}:{draft.revision}:"
    rows = []
    if draft.step == "review":
        text = review_text(draft)
        rows = [[("✅ Создать подписку", prefix + "confirm")],
                [("✏️ Имя", prefix + "edit_name"), ("📞 Телефон", prefix + "edit_phone")],
                [("✉️ Почта", prefix + "edit_email"), ("📅 Срок", prefix + "edit_period")],
                [("📦 Трафик", prefix + "edit_quota")]]
    else:
        prompts = {"name": "Отправьте имя владельца подписки.", "phone": "Отправьте номер телефона или пропустите этот шаг.",
                   "email": "Отправьте почту или пропустите этот шаг.", "period": "Выберите срок действия.",
                   "date": "Отправьте дату окончания: ДД.ММ.ГГГГ (включительно, по UTC).", "quota": "Выберите месячный лимит трафика.",
                   "quota_custom": "Отправьте объём трафика в ГБ, например 100 или 25,5."}
        text = f"<b>➕ Новая подписка</b>\n\n{prompts[draft.step]}"
        if draft.step == 'name':
            text += f"\n\nСрок: {PERIODS[draft.values.get('subscription_period','month')]}. Трафик: {traffic_label(draft.values.get('monthly_traffic_limit',0))}. Перед созданием сможете изменить условия и добавить контакты."
            rows.append([('📑 Выбрать готовый шаблон','ops:templates')])
        if draft.step in ("phone", "email"):
            rows = [[("Пропустить", prefix + "skip")]]
        elif draft.step == "period":
            rows = [[("Неделя", prefix + "period_week"), ("Месяц", prefix + "period_month")],
                    [("Год", prefix + "period_year"), ("Без срока", prefix + "period_unlimited")], [("Своя дата", prefix + "period_custom")]]
        elif draft.step == "quota":
            rows = [[("∞ Без ограничений", prefix + "quota_0")],
                    [("50 ГБ", prefix + "quota_50"), ("100 ГБ", prefix + "quota_100"), ("500 ГБ", prefix + "quota_500")],
                    [("Свой объём", prefix + "quota_custom")]]
        if draft.step != "name":
            rows.append([("← Назад", prefix + "back")])
    rows.append([("✖ Отмена", prefix + "cancel")])
    if error:
        text = escape(str(error)[:300]) + '\n\n' + text
    screen = await show_screen(message, text, keyboard(*rows), edit=edit)
    if isinstance(getattr(screen, 'message_id', None), int):
        draft.message = screen.message_id


async def start(message, actor, *, edit=False, values=None):
    clean_drafts()
    draft = Draft(actor, message.chat.id, message.message_id, values={
        'subscription_period': 'month', 'monthly_traffic_limit': 0, 'phone': '', 'email': '', **(values or {})})
    drafts[(actor, message.chat.id)] = draft
    await render(draft, message, edit=edit)


async def new_command(message):
    await start(message, message.from_user.id)


def next_step(draft, step):
    if step == 'period' and draft.values.get('_template'):
        step = 'review'
    # Редактирование из итоговой карточки возвращается сразу к подтверждению.
    if draft.values.pop("_editing", False):
        draft.step = "review"
    else:
        draft.step = step


async def text_input(message):
    draft = get_draft(message.from_user.id, message.chat.id)
    if not draft:
        return
    if draft.step not in ("name", "phone", "email", "date", "quota_custom"):
        await render(draft,message,edit=False,error="Выберите вариант кнопкой в меню подписки.")
        return
    try:
        value = validate_text(draft.step, message.text)
    except ValueError as exc:
        await render(draft,message,edit=False,error=exc)
        return
    mapping = {"name": ("name", "review"), "phone": ("phone", "email"), "email": ("email", "period"),
               "date": ("expires_at", "quota"), "quota_custom": ("monthly_traffic_limit", "review")}
    key, following = mapping[draft.step]
    draft.values[key] = value
    next_step(draft, following)
    # Обработанный ввод убирается middleware; экран меню переиспользуется.
    await render(draft, message, edit=False)


async def issue_access(message, client_id, *, awg=False, return_to=None):
    from app.api.clients import get_client_profiles
    from .handlers import keyboard, show_screen
    back = keyboard([('← Назад', return_to or f'ops:detail:{client_id}')])
    async with AsyncSessionLocal() as session:
        data = await get_client_profiles(client_id, db=session)
    if awg:
        profile = next((p for p in data["profiles"] if p["kind"] == "awg" and p["is_enabled"] and p["key_available"]), None)
        if not profile or not profile["data"]:
            await show_screen(message, "Для этой подписки нет доступного профиля AmneziaWG.", back, edit=True)
            return
        from .chat_screen import remember_attachment
        sent = await message.answer_document(BufferedInputFile(profile["data"].encode(), filename=f"subscription-{client_id}.conf"),
                                            caption="Сохраните файл и импортируйте его в AmneziaWG. Вернуться к меню — кнопкой ниже.", reply_markup=back)
        await remember_attachment(message, sent)
        return
    url = data["subscription_url"] or ''
    if not url.startswith("https://"):
        await show_screen(message, "Адрес панели не настроен. Данные подключения доступны в панели.", back, edit=True)
        return
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📲 Открыть подключение и QR-код', url=url)],
        [InlineKeyboardButton(text='← Назад', callback_data=return_to or f'ops:detail:{client_id}')]])
    await show_screen(message, f"<b>🔗 Подключение · {escape(data['client']['name'])}</b>\n\n"
                      "Откройте личную страницу: там есть импорт в приложение, QR-код и инструкция.\n\n"
                      f"Ссылка для копирования:\n<code>{escape(url)}</code>\n\n"
                      "Не передавайте ссылку посторонним: она содержит ваш доступ.", buttons, edit=True)


async def create_subscription(message, draft):
    # Панель и бот используют одну проверку полей, транзакцию и синхронизацию
    # протоколов. Не создаём сначала бессрочного клиента с последующим обновлением.
    from app.api.clients import ClientCreate, create_client_record
    from .handlers import back_menu
    values = {k: v for k, v in draft.values.items() if not k.startswith("_")}
    created_id = None
    try:
        async with AsyncSessionLocal() as session:
            result = await create_client_record(ClientCreate(**values), db=session)
            created_id = result["client"]["id"]
        from .admin_tools import detail as client_card
        await client_card(message, created_id)
    except Exception as exc:
        logging.error("Ошибка подписки Telegram: %s", type(exc).__name__)
        text = (f"Подписка #{created_id} создана. Не удалось выдать все данные; откройте её в списке подписок."
                if created_id is not None else "Не удалось создать подписку. Проверьте состояние протоколов в панели и попробуйте снова.")
        from .handlers import show_screen
        await show_screen(message, text, back_menu(), edit=True)


async def listing(message, page=0, *, edit=True):
    from .admin_tools import client_list
    return await client_list(message, message.chat.id, page)


async def detail(message, client_id, *, edit=True):
    from .handlers import keyboard, show_screen
    async with AsyncSessionLocal() as session:
        client = await session.get(Client, client_id)
        if not client:
            await listing(message)
            return
        limits = limit_info(client)
        status = {None: "🟢 Доступна", "disabled": "⏸ Приостановлена", "expired": "⌛ Срок истёк", "monthly_quota": "📦 Лимит исчерпан"}[limits['blocked_reason']]
        text = (f"<b>📋 {escape(client.name)}</b> · #{client.id}\n\n{status}\n"
                f"📞 {escape(client.phone or 'Не указан')}\n✉️ {escape(client.email or 'Не указана')}\n"
                f"📅 До: <b>{date_label(limits['expires_at'])}</b>\n"
                f"📦 За месяц: <b>{limits['monthly_traffic_used'] / 1024 ** 3:.2f} ГБ / {traffic_label(limits['monthly_traffic_limit'])}</b>\n"
                f"🛡 Обход БС: <b>{limits['cdn_monthly_traffic_used'] / 1024 ** 3:.2f} ГБ / {traffic_label(limits['cdn_monthly_traffic_limit'])}</b>\n"
                f"🔄 Новый период: {date_label(limits['traffic_period_end'])}")
    return await show_screen(message, text, keyboard(
        [("📲 Выдать подключение", f"sub:link:{client_id}"), ("📅 Продлить", f"ops:extend:{client_id}")],
        [("✏️ Срок и лимиты", f"ops:conditions:{client_id}"), ("🔐 Профили", f"ops:profiles:{client_id}")],
        [("⏸ Приостановить подписку" if client.is_active else "▶️ Включить подписку", f"ops:toggle:{client_id}")],
        [("⋯ Дополнительно", f"ops:more:{client_id}")],
        [("← Подписки", "ops:list:0"), ("🏠 Меню", "md:home")]), edit=edit)


def matching_draft(callback):
    parts = callback.data.split(":", 4)
    draft = get_draft(callback.from_user.id, callback.message.chat.id)
    if len(parts) != 5 or not draft or parts[2] != draft.token or parts[3] != str(draft.revision) or callback.message.message_id != draft.message:
        return None, None
    return draft, parts[4]


async def callback_input(callback):
    action = callback.data
    if action.startswith("sub:w:"):
        draft, command = matching_draft(callback)
        if not draft:
            await callback.answer("Эта кнопка устарела. Используйте последнее меню или создайте подписку заново.", show_alert=True)
            return
        # Инвалидация версии до первого await предотвращает двойное действие.
        draft.revision += 1
        if command == "confirm" and draft.step == "review":
            drafts.pop((draft.actor, draft.chat), None)
            await callback.answer("Создаю подписку…")
            await callback.message.edit_text("⏳ Создаю подписку…")
            await create_subscription(callback.message, draft)
            return
        await callback.answer()
        if command == "cancel":
            drafts.pop((draft.actor, draft.chat), None)
            await listing(callback.message)
            return
        if command == "skip" and draft.step in ("phone", "email"):
            draft.values[draft.step] = ""
            next_step(draft, "email" if draft.step == "phone" else "period")
        elif command == "back":
            draft.values.pop("_editing", None)
            draft.step = {"phone": "name", "email": "phone", "period": "email", "date": "period", "quota": "period", "quota_custom": "quota", "review": "quota"}.get(draft.step, "name")
        elif command.startswith("edit_") and draft.step == "review":
            step = command.removeprefix("edit_")
            if step in STEPS[:-1]:
                draft.step = step
                draft.values["_editing"] = True
        elif command.startswith("period_") and draft.step == "period":
            period = command.removeprefix("period_")
            if period in PERIODS:
                draft.values["subscription_period"] = period
                draft.values.pop("expires_at", None)
                if period == "custom":
                    draft.step = "date"
                else:
                    next_step(draft, "quota")
        elif command.startswith("quota_") and draft.step == "quota":
            value = command.removeprefix("quota_")
            if value == "custom":
                draft.step = "quota_custom"
            elif value in ("0", "50", "100", "500"):
                draft.values["monthly_traffic_limit"] = int(value) * 1024 ** 3
                next_step(draft, "review")
        await render(draft, callback.message)
        return
    # Выход из мастера отменяет черновик, а не оставляет скрытое ожидание текста.
    drafts.pop((callback.from_user.id, callback.message.chat.id), None)
    await callback.answer()
    if action == "sub:new":
        await start(callback.message, callback.from_user.id, edit=True)
    else:
        try:
            section, value = action.removeprefix("sub:").split(":", 1)
            value = int(value)
            if section == "list":
                await listing(callback.message, value)
            elif section == "detail":
                await detail(callback.message, value)
            elif section in ("link", "awg"):
                await issue_access(callback.message, value, awg=section == "awg")
        except (ValueError, LookupError):
            await listing(callback.message)
        except Exception as exc:
            logging.error("Ошибка выдачи подписки Telegram: %s", type(exc).__name__)
            from .handlers import show_screen,back_menu
            await show_screen(callback.message,"Не удалось выдать данные подписки. Обновите список и попробуйте снова.",back_menu(),edit=True)


def register(router):
    router.message.register(new_command, Command("new_subscription"))
    router.message.register(list_command, Command("subscriptions"))
    router.callback_query.register(callback_input, F.data.startswith("sub:"))
    router.message.register(text_input, F.text & ~F.text.startswith("/") & F.func(lambda m: get_draft(m.from_user.id, m.chat.id) is not None))


async def list_command(message):
    drafts.pop((message.from_user.id, message.chat.id), None)
    await listing(message, edit=False)
