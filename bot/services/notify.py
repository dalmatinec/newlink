import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from bot.config import Config
from bot.db import Database

log = logging.getLogger(__name__)


async def is_admin(db: Database, config: Config, user_id: int) -> bool:
    return user_id in config.super_admins or await db.is_admin(user_id)


async def safe_send(
    bot: Bot, db: Database, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> bool:
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        await db.set_blocked(chat_id, True)
    except TelegramAPIError as e:
        log.warning("send to %s failed: %s", chat_id, e)
    return False


async def notify_admins(
    bot: Bot, db: Database, config: Config, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    for admin_id in set(config.super_admins) | set(await db.list_admins()):
        await safe_send(bot, db, admin_id, text, reply_markup)
