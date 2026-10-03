import io
import time
import logging
import secrets
from dataclasses import dataclass
from html import escape
import qrcode
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.future import select
from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting
from app.services.telegram_settings import get_telegram_settings_from_db
from app.services.client_service import ClientService
from app.services.cluster import apply_active_node
from . import subscriptions

router = Router()


def keyboard(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


def main_menu():
    return keyboard(
        [("➕ Новая подписка", "sub:new")],
        [("📋 Подписки", "md:clients"), ("📊 Статус", "md:status")],
        [("🌐 Ноды", "md:nodes"), ("❔ Помощь", "md:help")],
    )


def back_menu():
    return keyboard([("🏠 Главное меню", "md:home")])


async def show_screen(message, text, markup, *, edit=False):
    # У файла конфигурации нет текстового тела: открываем меню отдельным сообщением.
    if edit and isinstance(message, Message) and message.text is None:
        await message.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    if edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=markup)


@dataclass
class PendingAction:
    actor_id: int
    chat_id: int
    message_id: int
    action: str
    node_id: int | None
    expires_at: float


pending_actions: dict[str, PendingAction] = {}


def prepare_action(callback, action, node_id=None):
    now = time.monotonic()
    for token, pending in list(pending_actions.items()):
        if pending.expires_at <= now or (pending.chat_id, pending.message_id) == (callback.message.chat.id, callback.message.message_id):
            pending_actions.pop(token, None)
    token = secrets.token_hex(8)
    pending_actions[token] = PendingAction(callback.from_user.id, callback.message.chat.id,
                                          callback.message.message_id, action, node_id, now + 120)
    return keyboard([("✅ Подтвердить", "md:confirm:" + token)], [("↩️ Отмена", "md:home")])


def consume_action(callback):
    token = callback.data.removeprefix("md:confirm:")
    pending = pending_actions.get(token)
    if not pending or pending.expires_at <= time.monotonic():
        pending_actions.pop(token, None)
        return None
    if (pending.actor_id, pending.chat_id, pending.message_id) != (callback.from_user.id, callback.message.chat.id, callback.message.message_id):
        return None
    # Удаление до первого await защищает от двух параллельных кликов.
    return pending_actions.pop(token)

@router.message.outer_middleware()
@router.callback_query.outer_middleware()
async def check_admin_middleware(handler, event, data):
    tg_settings = await get_telegram_settings_from_db()
    admin_id = tg_settings["admin_id"]
    if admin_id == 0 or not event.from_user or event.from_user.id != admin_id:
        if isinstance(event, CallbackQuery):
            await event.answer("Доступ запрещён. Бот только для администратора.", show_alert=True)
        else:
            await event.answer("Доступ запрещён. Бот только для администратора.")
        return
    message = event.message if isinstance(event, CallbackQuery) else event
    if not isinstance(message, Message) or message.chat.type != "private":
        await event.answer("Откройте личный диалог с ботом.", **({"show_alert": True} if isinstance(event, CallbackQuery) else {}))
        return
    return await handler(event, data)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message):
    subscriptions.drafts.pop((message.from_user.id, message.chat.id), None)
    await show_screen(message, "<b>MD-Next · Панель управления</b>\n\n"
                      "Подписки, сроки, трафик и выход через ноды.\nВыберите раздел ниже 👇", main_menu())


@router.message(Command("status"))
async def cmd_status(message: Message):
    await show_screen(message, await status_text(), keyboard(
        [("🔄 Обновить", "md:status")], [("🏠 Главное меню", "md:home")]))


async def status_text():
    async with AsyncSessionLocal() as session:
        setting_result = await session.execute(select(Setting).where(Setting.key == "active_node_id"))
        setting = setting_result.scalar_one_or_none()

        active_node_text = "Не задана"
        if setting and setting.value in {"direct:manual", "direct:auto"}:
            active_node_text = "Прямой выход с мастер-сервера"
        elif setting and setting.value:
            try:
                node_result = await session.execute(select(Node).where(Node.id == int(setting.value)))
                active_node = node_result.scalar_one_or_none()
                if active_node:
                    active_node_text = f"{escape(active_node.name)} (ID: {active_node.id})"
            except ValueError:
                pass

        nodes_res = await session.execute(select(Node))
        nodes = nodes_res.scalars().all()
        nodes_list = "\n".join([f"{'✅' if n.is_active and n.is_enabled else '⚪' if not n.is_enabled else '❌'} {escape(n.name)}" for n in nodes])

    return ("<b>📊 Состояние MD-Next</b>\n\n"
            f"<b>Текущий выход</b>\n{active_node_text}\n\n"
            f"<b>Ноды</b>\n{nodes_list or 'Нет узлов'}")


@router.message(Command("failover"))
async def cmd_failover(message: Message):
    async with AsyncSessionLocal() as session:
        setting_result = await session.execute(select(Setting).where(Setting.key == "active_node_id"))
        setting = setting_result.scalar_one_or_none()

        try:
            current_node_id = int(setting.value) if setting and setting.value else 0
        except ValueError:
            current_node_id = 0

        result = await session.execute(
            select(Node)
            .where(Node.is_active == True)
            .where(Node.is_enabled == True)
            .where(Node.id != current_node_id)
            .order_by(Node.id)
        )
        next_node = result.scalars().first()

        if next_node:
            ok, detail = await apply_active_node(session, next_node)
            if not ok:
                await message.answer("❌ Не удалось применить переключение. Текущий выход сохранён.")
                return
            await message.answer(f"✅ Выход переключён.\nНовая активная нода: <b>{escape(next_node.name)}</b>", parse_mode="HTML", reply_markup=back_menu())
        else:
            await message.answer("❌ Ошибка: нет других активных нод для переключения.")


@router.message(Command("add_vless"))
async def cmd_add_vless(message: Message):
    await subscriptions.start(message, message.from_user.id)


@router.message(Command("add_awg"))
async def cmd_add_awg(message: Message):
    await subscriptions.start(message, message.from_user.id)


async def create_client(message, actor_id, kind):
    created = False
    async with AsyncSessionLocal() as session:
        try:
            client_name = f"tg_{actor_id}_{secrets.token_hex(4)}"
            if kind == "awg":
                client, conf = await ClientService.create_awg_client(session, name=client_name)
                created = True
                await message.answer_document(
                    document=BufferedInputFile(conf.encode('utf-8'), filename=f"{client.name}.conf"),
                    caption=f"✅ Клиент AmneziaWG {client.name} создан!", reply_markup=back_menu())
                return
            client, link = await ClientService.create_vless_client(session, name=client_name)
            created = True

            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(link)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            bio = io.BytesIO()
            img.save(bio, "PNG")
            bio.seek(0)

            photo = BufferedInputFile(bio.read(), filename="qr.png")

            await message.answer_photo(
                photo=photo,
                caption=f"✅ Клиент VLESS <b>{escape(client.name)}</b> создан!",
                parse_mode="HTML"
            )
            await message.answer(f"<b>Ссылка подключения</b>\n\n<code>{escape(link)}</code>", parse_mode="HTML", reply_markup=back_menu())
        except Exception as e:
            logging.error("Ошибка создания или выдачи клиента Telegram: %s", type(e).__name__)
            text = ("Клиент создан, но не удалось выдать все данные. Откройте его в панели MD-Next."
                    if created else "❌ Не удалось создать клиента. Проверьте настройки протокола в панели.")
            await message.answer(text, reply_markup=back_menu())


async def nodes_screen(message):
    async with AsyncSessionLocal() as session:
        nodes = (await session.execute(select(Node).where(Node.is_enabled.is_(True), Node.is_active.is_(True)).order_by(Node.priority, Node.id))).scalars().all()
    rows = [[("🌐 " + node.name[:45], f"md:node:{node.id}")] for node in nodes]
    rows.append([("🏠 Главное меню", "md:home")])
    await show_screen(message, "<b>🌐 Выбор выхода</b>\n\n"
                      + ("Выберите ноду для трафика клиентов. Переключение применяется после подтверждения.\nВыход самого бота задаётся отдельно в панели."
                         if nodes else "Сейчас нет доступных включённых нод."), keyboard(*rows), edit=True)


@router.callback_query(F.data.startswith("md:"))
async def menu_callback(callback: CallbackQuery):
    action = callback.data
    message = callback.message
    subscriptions.drafts.pop((callback.from_user.id, message.chat.id), None)
    if action.startswith("md:confirm:"):
        pending = consume_action(callback)
        if not pending:
            await callback.answer("Подтверждение истекло или уже использовано. Откройте меню заново.", show_alert=True)
            return
        await callback.answer("Выполняю…")
        await show_screen(message, "⏳ <b>Применяю действие…</b>", None, edit=True)
        if pending.action in ("vless", "awg"):
            await create_client(message, callback.from_user.id, pending.action)
            await show_screen(message, "<b>👥 Клиенты</b>\n\nРезультат создания отправлен отдельным сообщением.", back_menu(), edit=True)
        else:
            async with AsyncSessionLocal() as session:
                node = await session.get(Node, pending.node_id)
                if not node or not node.is_enabled or not node.is_active:
                    text = "❌ Выбранная нода больше недоступна. Текущий выход сохранён."
                else:
                    try:
                        ok, detail = await apply_active_node(session, node)
                        text = f"✅ Выход переключён на <b>{escape(node.name)}</b>." if ok else "❌ Не удалось применить переключение. Текущий выход сохранён."
                    except Exception as exc:
                        logging.error("Ошибка переключения из Telegram: %s", type(exc).__name__)
                        text = "❌ Не удалось применить переключение. Проверьте состояние выхода в панели."
            await show_screen(message, text, back_menu(), edit=True)
        return

    await callback.answer()
    # Уход с экрана отменяет ещё не подтверждённое действие этого сообщения.
    for token, pending in list(pending_actions.items()):
        if (pending.chat_id, pending.message_id) == (message.chat.id, message.message_id):
            pending_actions.pop(token, None)
    if action == "md:home":
        await show_screen(message, "<b>MD-Next · Панель управления</b>\n\nВыберите раздел ниже 👇", main_menu(), edit=True)
    elif action == "md:status":
        await show_screen(message, await status_text(), keyboard([("🔄 Обновить", "md:status")], [("🏠 Главное меню", "md:home")]), edit=True)
    elif action == "md:clients":
        await subscriptions.listing(message)
    elif action in ("md:new:vless", "md:new:awg"):
        await subscriptions.start(message, callback.from_user.id, edit=True)
    elif action == "md:nodes":
        await nodes_screen(message)
    elif action.startswith("md:node:"):
        try:
            node_id = int(action.removeprefix("md:node:"))
        except ValueError:
            await show_screen(message, "Кнопка устарела. Откройте меню заново.", back_menu(), edit=True)
            return
        async with AsyncSessionLocal() as session:
            node = await session.get(Node, node_id)
            if not node or not node.is_enabled or not node.is_active:
                await show_screen(message, "Нода недоступна. Выберите другую.", back_menu(), edit=True)
                return
            await show_screen(message, f"<b>🌐 Переключить выход на {escape(node.name)}?</b>\n\n"
                              "Маршрут клиентов изменится. Подключения могут кратковременно прерваться.", prepare_action(callback, "node", node.id), edit=True)
    elif action == "md:help":
        await show_screen(message, "<b>❔ Возможности бота</b>\n\n"
                          "📋 Список подписок и карточки доступа\n➕ Мастер: имя, телефон, почта, срок и трафик\n🔗 Ссылка подписки и QR-код\n🛡 Файл AmneziaWG\n📊 Состояние и выбор выхода\n🔔 Уведомления о сбоях и лимитах\n\n"
                          "<b>Команды</b>\n/menu — меню\n/subscriptions — подписки\n/new_subscription — новая подписка\n/status — состояние\n/failover — следующая нода\n\n"
                          "Телефон и почту можно пропустить. Месячный трафик по умолчанию без ограничений. Создание требует подтверждения; черновик хранится 15 минут. /add_vless и /add_awg открывают мастер подписки.", back_menu(), edit=True)
    else:
        await show_screen(message, "Кнопка устарела. Откройте меню заново.", back_menu(), edit=True)


async def notify_admin(bot, text: str, notification_type: str = "failover"):
    tg_settings = await get_telegram_settings_from_db()
    admin_id = tg_settings["admin_id"]

    if notification_type == "node_down" and not tg_settings["notify_node_down"]:
        return
    if notification_type == "failover" and not tg_settings["notify_failover"]:
        return

    if admin_id != 0 and bot is not None:
        try:
            await bot.send_message(admin_id, f"⚠️ *ВНИМАНИЕ*\n\n{text}", parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление Telegram: {e}")


subscriptions.register(router)
