import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import BaseFilter
from aiogram.types import Message, ReactionTypeEmoji

import database as db
from utils import render, who

router = Router(name="group")
log = logging.getLogger(__name__)

# Типы, у которых есть подпись — их можно отправить одним сообщением.
_CAPTION_TYPES = {
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.ANIMATION,
    ContentType.AUDIO,
    ContentType.DOCUMENT,
    ContentType.VOICE,
}
_TEXT_LIMIT = 4096
_CAPTION_LIMIT = 1024


class IsSupportGroup(BaseFilter):
    """Сообщение из текущей группы поддержки (её можно менять на лету)."""

    async def __call__(self, message: Message) -> bool:
        group_id = db.get_group_id()
        return bool(group_id) and message.chat.id == group_id


router.message.filter(IsSupportGroup())


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message) -> None:
    # Группа стала супергруппой — у неё новый id, переезжаем вместе с ней.
    db.set_group_id(message.migrate_to_chat_id)


async def _deliver(bot: Bot, user_id: int, message: Message) -> None:
    """Отправляет ответ из группы пользователю в виде
    «Ответил @username: текст». Без ограничений по количеству."""
    body = message.html_text if (message.text or message.caption) else ""
    header = render("reply", who=who(message.from_user), text="")

    if message.text:
        full = render("reply", who=who(message.from_user), text=body)
        if len(full) <= _TEXT_LIMIT:
            await bot.send_message(user_id, full)
        else:
            await bot.send_message(user_id, header)
            await bot.send_message(user_id, body)
        return

    if message.content_type in _CAPTION_TYPES:
        full = render("reply", who=who(message.from_user), text=body)
        if len(full) <= _CAPTION_LIMIT:
            await bot.copy_message(user_id, message.chat.id, message.message_id, caption=full)
            return

    # Стикеры, кружки и т.п. — сначала подпись, потом само сообщение.
    await bot.send_message(user_id, header)
    await bot.copy_message(user_id, message.chat.id, message.message_id)


@router.message(F.reply_to_message, ~F.text.startswith("/"))
async def group_reply(message: Message, bot: Bot) -> None:
    replied = message.reply_to_message
    user_id = db.find_user_by_message(message.chat.id, replied.message_id)
    if user_id is None:
        return  # обычный разговор в группе, не ответ пользователю

    try:
        await _deliver(bot, user_id, message)
    except TelegramForbiddenError:
        await message.reply("❌ Не доставлено: пользователь заблокировал бота.")
        return
    except TelegramAPIError as e:
        log.warning("Не удалось доставить ответ пользователю %s: %s", user_id, e)
        await message.reply(f"❌ Не доставлено: {escape(e.message)}")
        return

    # Реплай на этот ответ коллеги тоже уйдёт тому же пользователю.
    db.map_message(message.chat.id, message.message_id, user_id)
    db.bump_stat("replies")
    try:
        await bot.set_message_reaction(
            message.chat.id, message.message_id, [ReactionTypeEmoji(emoji="👍")]
        )
    except TelegramAPIError:
        pass
