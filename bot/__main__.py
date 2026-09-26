import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, TelegramObject

from bot.config import load_config
from bot.db import Database
from bot.handlers import admin, chat_events, user
from bot.services.invites import cleanup_loop


class TrackUsers(BaseMiddleware):
    """Запоминает каждого, кто пишет боту в личку или жмёт кнопки."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        u = data.get("event_from_user")
        chat = data.get("event_chat")
        if u and not u.is_bot and (chat is None or chat.type == ChatType.PRIVATE):
            await data["db"].touch_user(u.id, u.first_name, u.username)
        return await handler(event, data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    db = await Database.connect(config.db_path)
    bot = Bot(
        config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=MemoryStorage(), db=db, config=config)
    dp.message.outer_middleware(TrackUsers())
    dp.callback_query.outer_middleware(TrackUsers())
    dp.include_routers(chat_events.router, admin.router, user.router)

    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    cleanup = asyncio.create_task(cleanup_loop(bot, db))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
