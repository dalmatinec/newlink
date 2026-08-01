from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

import database as db
from services.ad_service import get_due_ad
from services.json_store import load
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show

router = Router(name="start")

TEXTS_FILE = "texts.json"
LINKS_FILE = "links.json"


async def _show_main_menu(bot, chat_id: int) -> None:
    links = load(LINKS_FILE)
    main_menu = links.get("menus", {}).get("main", {})
    keyboard = build_inline_keyboard(main_menu.get("buttons", []))
    texts = load(TEXTS_FILE)
    greeting = texts.get("greeting", {"type": "none", "text": "👋 Добро пожаловать!"})
    await show(bot, chat_id, greeting, keyboard)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    db.upsert_user(message.from_user.id, message.from_user.username)
    db.bump_stat("starts")

    due = get_due_ad(message.from_user.id)
    if due:
        slot_id, slot = due
        keyboard = build_inline_keyboard(
            slot.get("buttons", []) + [{"id": "ad_continue", "label": "▶️ Продолжить", "type": "callback", "value": "ad:continue", "row": 99}]
        )
        await show(message.bot, message.chat.id, slot["content"], keyboard)
        import database_admin as db_admin

        db_admin.register_ad_show(message.from_user.id, slot_id)
        return

    await _show_main_menu(message.bot, message.chat.id)


@router.callback_query(F.data == "ad:continue")
async def ad_continue(callback: CallbackQuery) -> None:
    await callback.answer()
    await _show_main_menu(callback.bot, callback.message.chat.id)


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
