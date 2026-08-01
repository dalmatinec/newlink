import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import database as db
import database_admin as db_admin
from admin.admin_menu import is_admin
from services.content_extractor import extract_content
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show
from states import BroadcastStates
from text_format import separator

router = Router(name="admin_broadcast")

BTN_TYPE_LABELS = {
    "url": "🌐 Ссылка (URL)",
    "callback": "🔘 Callback",
    "copy": "📋 Копировать текст",
    "share": "📤 Поделиться",
}

MEDIA_SENDERS = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "document": "send_document",
    "voice": "send_voice",
}


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_entry(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await state.set_state(BroadcastStates.waiting_content)
    await state.update_data(buttons=[])

    text = (
        "📨 <b>Рассылка</b>\n"
        f"{separator()}\n"
        "Перешлите готовый пост (форвард), либо пришлите текст/фото/видео/GIF/"
        "документ/голосовое с подписью вручную."
    )
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:menu", "row": 1}]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(BroadcastStates.waiting_content)
async def broadcast_content(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    content = extract_content(message)
    await state.update_data(content=content)
    await _render_buttons_step(message.bot, message.chat.id, state)


async def _render_buttons_step(bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.waiting_buttons_choice)
    data = await state.get_data()
    buttons = data.get("buttons", [])

    lines = ["🔘 <b>Кнопки рассылки</b>", separator()]
    if buttons:
        for i, b in enumerate(buttons, start=1):
            lines.append(f"{i}. {b['label']} ({BTN_TYPE_LABELS.get(b['type'], b['type'])})")
    else:
        lines.append("Пока не добавлено ни одной кнопки.")
    text = "\n".join(lines)

    keyboard = build_inline_keyboard(
        [
            {"id": "add_btn", "label": "➕ Добавить кнопку", "type": "callback", "value": "bc:add_btn", "row": 1},
            {"id": "preview", "label": "👀 Предпросмотр и отправка", "type": "callback", "value": "bc:preview", "row": 2},
            {"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:menu", "row": 3},
        ]
    )
    await show(bot, chat_id, {"type": "none", "text": text}, keyboard)


@router.callback_query(BroadcastStates.waiting_buttons_choice, F.data == "bc:add_btn")
async def bc_add_btn_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.update_data(new_btn={})
    type_buttons = [
        {"id": f"t_{k}", "label": v, "type": "callback", "value": f"bc:type:{k}", "row": i + 1}
        for i, (k, v) in enumerate(BTN_TYPE_LABELS.items())
    ]
    keyboard = build_inline_keyboard(type_buttons)
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": "📎 Выберите тип кнопки."}, keyboard)
    await state.set_state(BroadcastStates.waiting_source)  # переиспользуем как "ждём тип"


@router.callback_query(BroadcastStates.waiting_source, F.data.startswith("bc:type:"))
async def bc_type_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    btype = callback.data.split(":", 2)[2]
    await state.update_data(new_btn={"type": btype})
    await callback.answer()
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": "✏️ Пришлите текст (label) кнопки."}, None)
    await state.set_state(BroadcastStates.waiting_confirm)  # "ждём label"


@router.message(BroadcastStates.waiting_confirm)
async def bc_label(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    if "new_btn" not in data or "label" in data["new_btn"]:
        return
    new_btn = data["new_btn"]
    new_btn["label"] = message.text
    await state.update_data(new_btn=new_btn)
    await state.set_state(BroadcastStates.waiting_buttons_choice)

    prompts = {
        "url": "🌐 Пришлите ссылку (https://...).",
        "callback": "🔘 Пришлите callback_data.",
        "copy": "📋 Пришлите текст, который будет копироваться.",
        "share": "📤 Пришлите текст предзаполнения (или «-»).",
    }
    await message.answer(prompts.get(new_btn["type"], "Пришлите значение."))


@router.message(BroadcastStates.waiting_buttons_choice)
async def bc_value_or_row(message: Message, state: FSMContext) -> None:
    """Ловим и значение кнопки, и номер ряда одним и тем же состоянием шага
    добавления, чтобы не плодить лишние состояния — определяем по данным в FSM."""
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    new_btn = data.get("new_btn")
    if not new_btn or "label" not in new_btn:
        return

    if "value" not in new_btn:
        new_btn["value"] = None if (message.text or "").strip() == "-" else message.text
        await state.update_data(new_btn=new_btn)
        await message.answer("🔢 В каком ряду разместить кнопку? Пришлите номер.")
        return

    if not message.text or not message.text.strip().isdigit():
        await message.answer("Пришлите целое число ряда, например 1.")
        return

    buttons = data.get("buttons", [])
    row = int(message.text.strip())
    position = sum(1 for b in buttons if b.get("row") == row) + 1
    new_btn["row"] = row
    new_btn["position"] = position
    buttons.append(new_btn)
    await state.update_data(buttons=buttons, new_btn=None)
    await _render_buttons_step(message.bot, message.chat.id, state)


@router.callback_query(F.data == "bc:preview")
async def bc_preview(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    content = data["content"]
    buttons = data.get("buttons", [])
    keyboard = build_inline_keyboard(buttons) if buttons else None

    await _send_content(callback.bot, callback.message.chat.id, content, keyboard)
    confirm_kb = build_inline_keyboard(
        [
            {"id": "send", "label": "✅ Отправить всем", "type": "callback", "value": "bc:send", "row": 1},
            {"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:menu", "row": 2},
        ]
    )
    await callback.message.answer("⬆️ Так рассылка выглядит у пользователя. Отправляем?", reply_markup=confirm_kb)


@router.callback_query(F.data == "bc:send")
async def bc_send(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer("🚀 Рассылка запущена")
    data = await state.get_data()
    content = data["content"]
    buttons = data.get("buttons", [])
    keyboard = build_inline_keyboard(buttons) if buttons else None
    await state.clear()

    bot = callback.bot
    user_ids = db_admin.all_user_ids()
    sent = blocked = errors = 0

    for user_id in user_ids:
        try:
            await _send_content(bot, user_id, content, keyboard)
            sent += 1
        except TelegramForbiddenError:
            db.set_blocked(user_id, True)
            blocked += 1
        except TelegramBadRequest:
            errors += 1
        await asyncio.sleep(0.05)  # грубый троттлинг, чтобы не упереться в лимиты Telegram

    db_admin.log_broadcast(sent, blocked, errors)
    report = (
        "📨 <b>Рассылка завершена</b>\n"
        f"{separator()}\n"
        f"✅ Успешно: {sent}\n"
        f"🚫 Заблокировали бота: {blocked}\n"
        f"⚠️ Ошибки: {errors}"
    )
    await callback.message.answer(report)


async def _send_content(bot: Bot, chat_id: int, content: dict, keyboard) -> None:
    ctype = content.get("type", "none")
    text = content.get("text") or "\u2063"
    file_id = content.get("file_id")

    if ctype == "video_note" and file_id:
        await bot.send_video_note(chat_id, file_id)
        if keyboard:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
    elif ctype in MEDIA_SENDERS and file_id:
        method = getattr(bot, MEDIA_SENDERS[ctype])
        await method(chat_id, file_id, caption=text, reply_markup=keyboard)
    else:
        await bot.send_message(chat_id, text, reply_markup=keyboard)
