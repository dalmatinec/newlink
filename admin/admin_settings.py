from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin.admin_menu import is_admin
from services.content_extractor import extract_content
from services.json_store import load, save
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show
from states import SettingsStates
from text_format import separator

router = Router(name="admin_settings")

TEXTS_FILE = "texts.json"
SETTINGS_FILE = "settings.json"


# ---------- Приветствие ----------

@router.callback_query(F.data == "admin:greeting")
async def greeting_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(SettingsStates.waiting_greeting)

    text = (
        "👋 <b>Редактирование приветствия</b>\n"
        f"{separator()}\n"
        "Пришлите новое приветствие одним сообщением: текст (HTML, жирный, "
        "курсив, ссылки, Premium Emoji), либо фото/видео/GIF/документ/"
        "голосовое/видеосообщение с подписью, либо просто текст без медиа."
    )
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:menu", "row": 1}]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(SettingsStates.waiting_greeting)
async def greeting_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    content = extract_content(message)
    texts = load(TEXTS_FILE)
    texts["greeting"] = content
    save(TEXTS_FILE, texts)
    await state.clear()

    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ В меню", "type": "callback", "value": "admin:menu", "row": 1}]
    )
    await show(message.bot, message.chat.id, {"type": "none", "text": "✅ Приветствие обновлено."}, keyboard)


# ---------- Общие настройки ----------

@router.callback_query(F.data == "admin:settings")
async def settings_menu(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await render_settings(callback.bot, callback.message.chat.id)


async def render_settings(bot, chat_id: int) -> None:
    settings = load(SETTINGS_FILE)
    ads_state = "🟢 включена" if settings.get("ads_enabled", True) else "🔴 выключена"
    mode = settings.get("default_invite_mode", "request")
    mode_label = "по заявке" if mode == "request" else "лимит на 1 человека"

    text = (
        "⚙️ <b>Настройки</b>\n"
        f"{separator()}\n"
        f"Реклама: {ads_state}\n"
        f"Режим ссылок по умолчанию: <b>{mode_label}</b>"
    )
    keyboard = build_inline_keyboard(
        [
            {"id": "toggle_ads", "label": "🔁 Вкл/выкл рекламу", "type": "callback", "value": "settings:toggle_ads", "row": 1},
            {"id": "toggle_mode", "label": "🔁 Сменить режим ссылок", "type": "callback", "value": "settings:toggle_mode", "row": 2},
            {"id": "back", "label": "⬅️ Назад", "type": "callback", "value": "admin:menu", "row": 3},
        ]
    )
    await show(bot, chat_id, {"type": "none", "text": text}, keyboard)


@router.callback_query(F.data == "settings:toggle_ads")
async def toggle_ads(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    settings = load(SETTINGS_FILE)
    settings["ads_enabled"] = not settings.get("ads_enabled", True)
    save(SETTINGS_FILE, settings)
    await callback.answer("Готово")
    await render_settings(callback.bot, callback.message.chat.id)


@router.callback_query(F.data == "settings:toggle_mode")
async def toggle_mode(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    settings = load(SETTINGS_FILE)
    settings["default_invite_mode"] = (
        "single_use" if settings.get("default_invite_mode") == "request" else "request"
    )
    save(SETTINGS_FILE, settings)
    await callback.answer("Готово")
    await render_settings(callback.bot, callback.message.chat.id)
