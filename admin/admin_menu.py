from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import SUPER_ADMIN_ID
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show

router = Router(name="admin_menu")


def is_admin(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID


ADMIN_BUTTONS = [
    {"id": "a_broadcast", "label": "📨 Рассылка", "type": "callback", "value": "admin:broadcast", "row": 1},
    {"id": "a_stats", "label": "📊 Статистика", "type": "callback", "value": "admin:stats", "row": 2},
    {"id": "a_ads", "label": "📣 Реклама", "type": "callback", "value": "admin:ads", "row": 3},
    {"id": "a_links", "label": "🔗 Кнопки и меню", "type": "callback", "value": "admin:links", "row": 4},
    {"id": "a_greeting", "label": "👋 Приветствие", "type": "callback", "value": "admin:greeting", "row": 5},
    {"id": "a_settings", "label": "⚙️ Настройки", "type": "callback", "value": "admin:settings", "row": 6},
]


async def render_admin_menu(bot, chat_id: int) -> None:
    keyboard = build_inline_keyboard(ADMIN_BUTTONS)
    await show(bot, chat_id, {"type": "none", "text": "🛠 <b>Панель администратора</b>"}, keyboard)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администратору.")
        return
    await render_admin_menu(message.bot, message.chat.id)


@router.callback_query(F.data == "admin:menu")
async def back_to_admin_menu(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await render_admin_menu(callback.bot, callback.message.chat.id)
