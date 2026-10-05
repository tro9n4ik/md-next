import asyncio
import logging
from typing import Optional
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

dp = Dispatcher()

class BotManager:
    """
    Менеджер динамического управления Telegram-ботом (запуск, остановка, перезапуск и валидация)
    """
    def __init__(self):
        self.bot: Optional[Bot] = None
        self.polling_task: Optional[asyncio.Task] = None
        self.router_setup = False
        self.status: str = "disabled"  # "disabled", "running", "error"
        self.last_error: Optional[str] = None

    def create_bot(self, token: str, proxy_url: Optional[str] = None) -> Bot:
        if proxy_url and proxy_url.strip():
            session = AiohttpSession(proxy=proxy_url.strip(), timeout=15)
            return Bot(token=token.strip(), session=session)
        return Bot(token=token.strip(), session=AiohttpSession(timeout=15))

    async def validate_token(self, token: str, proxy_url: Optional[str] = None):
        temp_bot = self.create_bot(token, proxy_url)
        try:
            me = await temp_bot.get_me()
            return me
        finally:
            await temp_bot.session.close()

    async def start(self, token: str, proxy_url: Optional[str] = None):
        await self.stop()
        if not token or not token.strip():
            self.status = "disabled"
            self.last_error = None
            return

        try:
            from . import handlers
            if not self.router_setup:
                from .portal import router as portal_router
                dp.include_router(portal_router)
                dp.include_router(handlers.router)
                self.router_setup = True

            self.bot = self.create_bot(token, proxy_url)
            await self.bot.get_me()
            try:
                await self.bot.set_my_commands([
                    BotCommand(command="menu", description="Панель управления"),
                    BotCommand(command="subscriptions", description="Список подписок"),
                    BotCommand(command="new_subscription", description="Создать подписку"),
                    BotCommand(command="status", description="Состояние и текущий выход"),
                    BotCommand(command="me", description="Моя подписка"),
                    BotCommand(command="bind", description="Привязать подписку по коду"),
                    BotCommand(command="diagnostics", description="Диагностика для администратора"),
                ])
            except Exception as exc:
                logger.warning("Не удалось обновить список команд бота: %s", type(exc).__name__)
            self.polling_task = asyncio.create_task(self._poll())
            # Даём Dispatcher войти в цикл, чтобы немедленное сохранение настроек
            # могло корректно остановить его через stop_polling.
            await asyncio.sleep(0)
            self.status = "running"
            self.last_error = None
            logger.info("Telegram-бот успешно запущен.")
        except Exception as e:
            self.status = "error"
            self.last_error = "Не удалось подключиться к Telegram. Проверьте токен и выбранный выход."
            logger.error("Ошибка при запуске Telegram-бота: %s", type(e).__name__)
            if self.bot:
                await self.bot.session.close()
                self.bot = None

    async def _poll(self):
        try:
            await dp.start_polling(self.bot, handle_signals=False, close_bot_session=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = "error"
            self.last_error = "Получение команд Telegram остановлено. Проверьте подключение."
            logger.error("Ошибка получения команд Telegram: %s", type(exc).__name__)

    async def stop(self):
        if self.polling_task:
            if not self.polling_task.done():
                try:
                    await dp.stop_polling()
                except RuntimeError:
                    self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
            self.polling_task = None

        if self.bot:
            try:
                await self.bot.session.close()
            except Exception:
                pass
            self.bot = None

        self.status = "disabled"

    async def reload(self, token: str, proxy_url: Optional[str] = None):
        await self.start(token, proxy_url)

    async def rebind_proxy(self, proxy_url: str):
        """Меняет транспорт текущего бота, в том числе из его собственного обработчика."""
        if self.bot is None:
            return
        previous = self.bot.session
        self.bot.session = AiohttpSession(proxy=proxy_url, timeout=15)
        try:
            await previous.close()
        except Exception:
            logger.warning("Не удалось закрыть прежний транспорт Telegram")

bot_manager = BotManager()

def get_bot() -> Optional[Bot]:
    return bot_manager.bot
