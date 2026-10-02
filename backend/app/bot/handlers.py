import io
import time
import logging
import qrcode
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, BufferedInputFile
from sqlalchemy.future import select
from app.db.database import AsyncSessionLocal
from app.models.node import Node
from app.models.setting import Setting
from app.services.telegram_settings import get_telegram_settings_from_db
from app.services.client_service import ClientService

router = Router()

@router.message.outer_middleware()
async def check_admin_middleware(handler, event, data):
    tg_settings = await get_telegram_settings_from_db()
    admin_id = tg_settings["admin_id"]
    if admin_id == 0 or event.from_user.id != admin_id:
        await event.answer("Доступ запрещен. Бот только для администратора.")
        return
    return await handler(event, data)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Добро пожаловать в панель MD-Next!\n\n"
        "Доступные команды:\n"
        "/status - Статус серверов и нод\n"
        "/failover - Ручное переключение ноды\n"
        "/add_vless - Создать клиента VLESS\n"
        "/add_awg - Создать клиента AWG\n"
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
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
                    active_node_text = f"{active_node.name} (ID: {active_node.id})"
            except ValueError:
                pass

        nodes_res = await session.execute(select(Node))
        nodes = nodes_res.scalars().all()
        nodes_list = "\n".join([f"• {n.name}: {'✅' if n.is_active else '❌'}" for n in nodes])

    text = f"📊 *СТАТУС MD-NEXT*\n\n" \
           f"🟢 Текущая активная нода (каскад): {active_node_text}\n\n" \
           f"🛠 Узлы кластера:\n{nodes_list if nodes_list else 'Нет узлов'}"

    await message.answer(text, parse_mode="Markdown")


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
            .where(Node.id != current_node_id)
            .order_by(Node.id)
        )
        next_node = result.scalars().first()

        if next_node:
            if setting:
                setting.value = str(next_node.id)
            else:
                session.add(Setting(key="active_node_id", value=str(next_node.id)))

            await session.commit()
            await message.answer(f"✅ Ручной failover выполнен.\nНовая активная нода: *{next_node.name}*", parse_mode="Markdown")
        else:
            await message.answer("❌ Ошибка: нет других активных нод для переключения.")


@router.message(Command("add_vless"))
async def cmd_add_vless(message: Message):
    async with AsyncSessionLocal() as session:
        try:
            client_name = f"tg_{message.from_user.id}_{int(time.time())}"
            client, link = await ClientService.create_vless_client(session, name=client_name)

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
                caption=f"✅ Клиент VLESS `{client.name}` создан!\n\n`{link}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка при создании VLESS клиента: {e}")


@router.message(Command("add_awg"))
async def cmd_add_awg(message: Message):
    async with AsyncSessionLocal() as session:
        try:
            client_name = f"tg_{message.from_user.id}_{int(time.time())}"
            client, conf = await ClientService.create_awg_client(session, name=client_name)

            conf_bytes = conf.encode('utf-8')
            doc = BufferedInputFile(conf_bytes, filename=f"{client.name}.conf")

            await message.answer_document(
                document=doc,
                caption=f"✅ Клиент AmneziaWG `{client.name}` создан!"
            )
        except Exception as e:
            await message.answer(f"❌ Ошибка при создании AWG клиента: {e}")


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
