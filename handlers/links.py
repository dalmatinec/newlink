from aiogram import F, Router
from aiogram.types import CallbackQuery

from services.json_store import load
from services.keyboard_builder import build_inline_keyboard
from services.link_generator import generate_invite_link
from services.navigator import show

router = Router(name="links")

TEXTS_FILE = "texts.json"
LINKS_FILE = "links.json"


def _find_button(button_id: str) -> dict | None:
    links = load(LINKS_FILE)
    for menu in links.get("menus", {}).values():
        for button in menu.get("buttons", []):
            if button["id"] == button_id:
                return button
    return None


@router.callback_query(F.data.startswith("link:"))
async def handle_generated_link(callback: CallbackQuery) -> None:
    button_id = callback.data.split(":", 1)[1]
    button = _find_button(button_id)
    texts = load(TEXTS_FILE)

    if not button:
        await callback.answer("Кнопка не найдена", show_alert=True)
        return

    await callback.answer("⏳ Генерирую ссылку...")

    try:
        mode = button.get("invite_mode", "request")
        link = await generate_invite_link(
            callback.bot,
            chat_id=button["chat_id"],
            mode=mode,
            duration_minutes=button.get("duration_minutes", 60),
        )
        key = "link_ready_single" if mode == "single_use" else "link_ready_request"
        text = texts.get(key, "🔗 {link}").format(link=link)
    except Exception:
        text = texts.get("link_error", "❌ Не удалось создать ссылку.")

    # разовое уведомление — не часть навигации, поэтому не через navigator
    await callback.message.answer(text)


@router.callback_query(F.data.startswith("menu:"))
async def handle_menu_navigation(callback: CallbackQuery) -> None:
    menu_id = callback.data.split(":", 1)[1]
    links = load(LINKS_FILE)
    menu = links.get("menus", {}).get(menu_id)
    texts = load(TEXTS_FILE)

    if not menu:
        await callback.answer(texts.get("menu_not_found", "Раздел не найден"), show_alert=True)
        return

    await callback.answer()
    keyboard = build_inline_keyboard(menu.get("buttons", []))
    content = menu.get("content", {"type": "none", "text": menu_id})
    await show(callback.bot, callback.message.chat.id, content, keyboard)
