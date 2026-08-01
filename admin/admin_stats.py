from aiogram import F, Router
from aiogram.types import CallbackQuery

import database_admin as db_admin
from admin.admin_menu import is_admin
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show
from text_format import separator

router = Router(name="admin_stats")


@router.callback_query(F.data == "admin:stats")
async def show_stats(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()

    s = db_admin.get_stats()
    sep = separator()
    text = (
        "📊 <b>Статистика бота</b>\n"
        f"{sep}\n"
        f"👥 Всего пользователей: <b>{s['total_users']}</b>\n"
        f"🟢 Активных: <b>{s['active']}</b>\n"
        f"🔴 Заблокировали бота: <b>{s['blocked']}</b>\n"
        f"{sep}\n"
        f"🆕 Новых сегодня: <b>{s['new_today']}</b>\n"
        f"🆕 Новых за неделю: <b>{s['new_week']}</b>\n"
        f"🆕 Новых за месяц: <b>{s['new_month']}</b>\n"
        f"{sep}\n"
        f"🚀 Запусков сегодня: <b>{s['starts_today']}</b>\n"
        f"🚀 Запусков за неделю: <b>{s['starts_week']}</b>\n"
        f"🚀 Запусков за месяц: <b>{s['starts_month']}</b>"
    )
    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ Назад", "type": "callback", "value": "admin:menu", "row": 1}]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)
