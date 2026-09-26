from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User

from bot.callbacks import OpenButton
from bot.db import Database
from bot.texts import DEFAULT_GREETING, EMPTY_MENU, render


async def build_menu(db: Database) -> InlineKeyboardMarkup | None:
    rows = [
        [InlineKeyboardButton(text=b["title"], callback_data=OpenButton(id=b["id"]).pack())]
        for b in await db.list_buttons(only_enabled=True)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send_greeting(bot: Bot, db: Database, chat_id: int, user: User) -> None:
    greeting = await db.get_setting("greeting", DEFAULT_GREETING)
    text = render(greeting.get("html") or "", user)
    menu = await build_menu(db)
    if menu is None:
        text = f"{text}\n\n{EMPTY_MENU}" if text else EMPTY_MENU

    kind = greeting.get("kind", "text")
    if kind == "text":
        await bot.send_message(chat_id, text or "👋", reply_markup=menu)
        return
    send = {
        "photo": bot.send_photo,
        "video": bot.send_video,
        "animation": bot.send_animation,
        "document": bot.send_document,
    }[kind]
    await send(chat_id, greeting["file_id"], caption=text or None, reply_markup=menu)
