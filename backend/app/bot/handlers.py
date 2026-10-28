import io
import time
import logging
import secrets
from dataclasses import dataclass
from html import escape
import qrcode
from qrcode.image.pure import PyPNGImage
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
        [("📋 Подписки", "ops:list:0")],
        [("⚙️ Система", "md:system"), ("❔ Помощь", "md:help")],
    )


def back_menu():
    return keyboard([("🏠 Главное меню", "md:home")])


def invalidate_screen_actions(chat_id, identifier, markup):
    callbacks={button.callback_data for row in markup.inline_keyboard for button in row} if markup else set()
    for token,value in list(pending_actions.items()):
        if (value.chat_id,value.message_id)==(chat_id,identifier) and 'md:confirm:'+token not in callbacks:
            pending_actions.pop(token,None)
    tools=globals().get('admin_tools')
    if tools:
        for token,value in list(tools.pending.items()):
            if (value['chat'],value['message'])==(chat_id,identifier) and 'ops:confirm:'+token not in callbacks:
                tools.pending.pop(token,None)


async def show_screen(message, text, markup, *, edit=False):
    if isinstance(message, Message):
        try:
            bound_bot = message.bot
        except RuntimeError:
            bound_bot = None
        if bound_bot is not None:
            from .chat_screen import live_message, remember, protected_message, message_id, clear_attachment, cleanup_legacy_cards
            await clear_attachment(bound_bot, message.chat.id)
            await cleanup_legacy_cards(bound_bot,message.chat.id)
            # Устаревшая кнопка не должна создавать второе рабочее меню.
            protected = await protected_message(bound_bot, message.chat.id, message.message_id)
            current_menu = await message_id(bound_bot, message.chat.id, 'menu')
            if edit and message.text is not None and message.from_user and message.from_user.is_bot and not protected and current_menu==message.message_id:
                try:
                    result = await message.edit_text(text, parse_mode='HTML', reply_markup=markup)
                except TelegramBadRequest as exc:
                    reason=exc.message.lower()
                    if 'message is not modified' in reason:
                        result = message
                    elif any(value in reason for value in ('message to edit not found',"message can't be edited",'message_id_invalid')):
                        result,identifier=await live_message(bound_bot,message.chat.id,text,markup,channel='menu',silent=True,new_message=True)
                        invalidate_screen_actions(message.chat.id,message.message_id,markup)
                        return result if isinstance(result,Message) else message.model_copy(update={'message_id':identifier,'text':text})
                    else:
                        raise
                await remember(bound_bot, message.chat.id, 'menu', message.message_id)
                invalidate_screen_actions(message.chat.id,message.message_id,markup)
                return result
            fresh_command = bool(not edit and message.text and message.text.startswith('/')
                                 and message.from_user and not message.from_user.is_bot)
            previous_menu = await message_id(bound_bot, message.chat.id, 'menu') if fresh_command else None
            result, identifier = await live_message(bound_bot, message.chat.id, text, markup,
                                                   channel='menu', silent=True, new_message=fresh_command)
            if previous_menu is not None and previous_menu != identifier:
                invalidate_screen_actions(message.chat.id, previous_menu, None)
            invalidate_screen_actions(message.chat.id,identifier,markup)
            return result if isinstance(result, Message) else message.model_copy(update={'message_id': identifier, 'text': text})
    # У файла конфигурации нет текстового тела: открываем меню отдельным сообщением.
    if edit and isinstance(message, Message) and message.text is None:
        return await message.answer(text, parse_mode="HTML", reply_markup=markup)
    if edit:
        try:
            return await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        return await message.answer(text, parse_mode="HTML", reply_markup=markup)


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
    if isinstance(event,Message) and (event.text or '').startswith('/'):
        subscriptions.drafts.pop((event.from_user.id,event.chat.id),None)
        admin_tools.inputs.pop((event.from_user.id,event.chat.id),None)
    result = await handler(event, data)
    if isinstance(event, Message):
        from .chat_screen import clean_input
        await clean_input(event)
    return result


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message):
    subscriptions.drafts.pop((message.from_user.id, message.chat.id), None)
    admin_tools.inputs.pop((message.from_user.id, message.chat.id), None)
    await show_screen(message, await home_text(), main_menu())


async def home_text():
    from app.models.client import Client
    from app.services.client_limits import limit_info
    async with AsyncSessionLocal() as db:
        clients = (await db.execute(select(Client))).scalars().all()
    active = sum(bool(limit_info(client)['access_allowed']) for client in clients)
    return ("<b>MD-Next · Управление подписками</b>\n\n"
            f"Всего подписок: <b>{len(clients)}</b>\nДоступны: <b>{active}</b> · Недоступны: <b>{len(clients)-active}</b>\n\n"
            "Создайте подписку или выберите существующую. Срок, трафик и подключение — в её карточке.")


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
        lines=[]
        for node in sorted(nodes, key=lambda n: (n.priority, n.id)):
            healthy = node.is_active and node.is_enabled and node.status == "healthy"
            label = "Доступна" if healthy else "Выключена" if not node.is_enabled else "Недоступна" if node.status in {"unhealthy", "unavailable"} else "Проверяется"
            delay = f" · {node.ping_ms} мс" if healthy else ""
            lines.append(f"{'🟢' if healthy else '⚪' if not node.is_enabled else '🟠'} <b>{escape(node.name)}</b>\n{label}{delay}")
        nodes_list = "\n\n".join(lines)

    return ("<b>📊 Состояние MD-Next</b>\n\n"
            f"<b>Текущий выход</b>\n{active_node_text}\n\n"
            f"<b>Ноды</b>\n{nodes_list or 'Пока не добавлены'}\n\n<i>Проверка со стороны сервера. Состояние устройств клиентов здесь не учитывается.</i>")


@router.message(Command("failover"))
async def cmd_failover(message: Message):
    # Даже старая команда открывает выбор с подтверждением, а не меняет маршрут сразу.
    await nodes_screen(message)


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
            img = qr.make_image(image_factory=PyPNGImage, fill_color="black", back_color="white")

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
                      + ("Выберите ноду для трафика клиентов. Переключение применяется после подтверждения.\nБот автоматически использует текущую ноду выхода."
                         if nodes else "Сейчас нет доступных включённых нод."), keyboard(*rows), edit=True)


@router.callback_query(F.data.startswith("md:"))
async def menu_callback(callback: CallbackQuery):
    action = callback.data
    message = callback.message
    subscriptions.drafts.pop((callback.from_user.id, message.chat.id), None)
    admin_tools.inputs.pop((callback.from_user.id, message.chat.id), None)
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
        admin_tools.inputs.pop((callback.from_user.id, message.chat.id), None)
        await show_screen(message, await home_text(), main_menu(), edit=True)
    elif action == "md:system":
        await show_screen(message, "<b>⚙️ Система</b>\n\nПроверьте состояние, выберите выход или запустите диагностику. Изменение ноды требует подтверждения.", keyboard(
            [("📊 Состояние", "md:status"), ("🌐 Выход через ноду", "md:nodes")],
            [("🩺 Проверить соединение", "ops:diagnostics"),('🔔 События','md:inbox')], [("🏠 Главное меню", "md:home")]), edit=True)
    elif action.startswith('md:inbox'):
        from .inbox import screen
        await screen(message,action.removeprefix('md:inbox').lstrip(':') or None)
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
        await show_screen(message, "<b>Как пользоваться ботом</b>\n\n"
                          "<b>Создать подписку</b> — введите имя, проверьте срок и лимит, подтвердите. Контакты добавляются по желанию.\n\n"
                          "<b>Подписки</b> — найдите клиента по имени, телефону или почте. В карточке можно выдать подключение, продлить срок, изменить лимиты и профили.\n\n"
                          "<b>Система</b> — состояние, выход через ноду и проверка соединения. Переключение требует подтверждения.\n\n"
                          "<b>Назад</b> возвращает на предыдущий экран. /menu восстанавливает меню. Обработанный ввод убирается из чата; файлы выдаются только по запросу.", back_menu(), edit=True)
    else:
        await show_screen(message, "Кнопка устарела. Откройте меню заново.", back_menu(), edit=True)


async def notify_admin(bot, text, notification_type: str = "failover", *, session=None, event_key="route"):
    from app.services.telegram_delivery import enqueue
    return await enqueue(text, notification_type, session=session, event_key=event_key)


from . import admin_tools
admin_tools.register(router)
subscriptions.register(router)


@router.message(F.text)
async def unknown_text(message: Message):
    subscriptions.drafts.pop((message.from_user.id,message.chat.id),None)
    admin_tools.inputs.pop((message.from_user.id,message.chat.id),None)
    await show_screen(message,await home_text()+'\n\nВыберите действие кнопкой. Для поиска откройте «Подписки → Найти».',main_menu())
