from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

import database as db

MEDIA_SENDERS = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "document": "send_document",
    "voice": "send_voice",
}


async def _delete_last(bot: Bot, chat_id: int) -> None:
    user = db.get_user(chat_id)
    last_id = user["last_bot_message_id"] if user else None
    if not last_id:
        return
    try:
        await bot.delete_message(chat_id, last_id)
    except TelegramBadRequest:
        # сообщение уже удалено юзером/устарело (>48ч) — не критично, просто идём дальше
        pass


async def show(bot: Bot, chat_id: int, content: dict, keyboard: InlineKeyboardMarkup | None = None):
    """
    Единая точка показа НАВИГАЦИОННЫХ экранов (меню/приветствие/реклама/разделы
    конструктора). Удаляет предыдущее такое сообщение и присылает новое —
    чтобы не засорять ЛС бота.

    content = {"type": "none|photo|video|animation|document|voice|video_note",
               "text": str, "file_id": str | None}

    Разовые уведомления (одобрение заявки, готовая ссылка и т.п.) через этот
    сервис не отправляются — для них используем обычный message.answer().
    """
    await _delete_last(bot, chat_id)

    ctype = content.get("type", "none")
    text = content.get("text") or "\u2063"  # невидимый символ, если текста нет вовсе
    file_id = content.get("file_id")

    if ctype == "video_note" and file_id:
        # video_note не поддерживает caption/reply_markup — шлём кружок,
        # а меню отдельным сообщением сразу следом
        await bot.send_video_note(chat_id, file_id)
        msg = await bot.send_message(chat_id, text, reply_markup=keyboard)
    elif ctype in MEDIA_SENDERS and file_id:
        method = getattr(bot, MEDIA_SENDERS[ctype])
        msg = await method(chat_id, file_id, caption=text, reply_markup=keyboard)
    else:
        msg = await bot.send_message(chat_id, text, reply_markup=keyboard)

    db.set_last_message_id(chat_id, msg.message_id)
    return msg
