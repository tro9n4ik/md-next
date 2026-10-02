import asyncio
import logging
from typing import Optional
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector

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
            connector = ProxyConnector.from_url(proxy_url.strip())
            session = AiohttpSession(connector=connector)
            return Bot(token=token.strip(), session=session)
        return Bot(token=token.strip())

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
                dp.include_router(handlers.router)
                self.router_setup = True

            self.bot = self.create_bot(token, proxy_url)
            self.polling_task = asyncio.create_task(dp.start_polling(self.bot))
            self.status = "running"
            self.last_error = None
            logger.info("Telegram-бот успешно запущен.")
        except Exception as e:
            self.status = "error"
            self.last_error = str(e)
            logger.error(f"Ошибка при запуске Telegram-бота: {e}")

    async def stop(self):
        if self.polling_task:
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

bot_manager = BotManager()

def get_bot() -> Optional[Bot]:
    return bot_manager.bot
