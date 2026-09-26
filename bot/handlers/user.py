from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import texts
from bot.callbacks import OpenButton
from bot.config import Config
from bot.db import Database, now
from bot.services.invites import (
    LinkTemporaryError,
    LinkUnavailable,
    get_or_create_link,
    is_member,
    mark_chat_broken,
)
from bot.services.menu import send_greeting
from bot.services.notify import notify_admins

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)


@router.message(CommandStart())
async def start(message: Message, bot: Bot, db: Database) -> None:
    await send_greeting(bot, db, message.chat.id, message.from_user)


@router.callback_query(OpenButton.filter())
async def open_button(
    cb: CallbackQuery, callback_data: OpenButton, bot: Bot, db: Database, config: Config
) -> None:
    button = await db.get_button(callback_data.id)
    if button is None or not button["enabled"]:
        await cb.answer("Эта кнопка больше недоступна.", show_alert=True)
        return
    if button["chat_id"] is None or button["broken"]:
        await cb.answer(texts.UNAVAILABLE, show_alert=True)
        return

    user_id = cb.from_user.id
    if await is_member(bot, button["chat_id"], user_id):
        await cb.answer(texts.ALREADY_MEMBER.format(title=button["title"]), show_alert=True)
        return

    try:
        invite = await get_or_create_link(bot, db, button, user_id)
    except LinkTemporaryError:
        await cb.answer(texts.TRY_LATER, show_alert=True)
        return
    except LinkUnavailable as e:
        await cb.answer(texts.UNAVAILABLE, show_alert=True)
        if await mark_chat_broken(db, button["chat_id"]):
            await notify_admins(
                bot, db, config,
                f"⚠️ Кнопка «{escape(button['title'])}» сломалась: не могу создать ссылку "
                f"(<code>{escape(str(e))}</code>).\n\nПривяжи новый чат: /admin → 🔘 Кнопки.",
            )
        return

    await cb.answer()
    await cb.message.answer(
        texts.link_message(button, invite["expires_at"], now()),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚀 Вступить", url=invite["link"])]]
        ),
    )
