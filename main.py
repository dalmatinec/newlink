import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database import init_db
from database_admin import init_admin_db
from services.cleanup import clean_temp

from handlers import start, links
from admin import admin_menu, admin_links, admin_ads, admin_broadcast, admin_settings, admin_stats


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    init_db()
    init_admin_db()
    clean_temp()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Добавление нового модуля — это ОДНА строка ниже, без изменения
    # остального проекта: from handlers.news import router; dp.include_router(router)
    dp.include_router(start.router)
    dp.include_router(links.router)

    dp.include_router(admin_menu.router)
    dp.include_router(admin_links.router)
    dp.include_router(admin_ads.router)
    dp.include_router(admin_broadcast.router)
    dp.include_router(admin_settings.router)
    dp.include_router(admin_stats.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
