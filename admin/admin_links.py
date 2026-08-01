from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin.admin_menu import is_admin
from emoji_utils import extract_custom_emoji_id
from services.chat_resolver import parse_chat_source
from services.json_store import load, save
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show
from states import ButtonStates
from text_format import separator

router = Router(name="admin_links")

LINKS_FILE = "links.json"
SETTINGS_FILE = "settings.json"

TYPE_ICONS = {
    "url": "🌐",
    "generated": "♻️",
    "menu": "📂",
    "callback": "🔘",
    "webapp": "🧩",
    "copy": "📋",
    "share": "📤",
}

TYPE_LABELS = {
    "url": "🌐 Обычная ссылка (URL)",
    "generated": "♻️ Генерируемая ссылка (чат/канал)",
    "menu": "📂 Переход в другое меню",
    "callback": "🔘 Callback-действие",
    "webapp": "🧩 WebApp",
    "copy": "📋 Копировать текст",
    "share": "📤 Поделиться",
}

CURRENT_MENU_ID = "main"  # на этом этапе конструктор работает с главным меню


def _menu(menu_id: str) -> dict:
    links = load(LINKS_FILE)
    return links.setdefault("menus", {}).setdefault(menu_id, {"content": {"type": "none", "text": menu_id}, "buttons": []})


def _save_links() -> None:
    save(LINKS_FILE, load(LINKS_FILE))


def _next_id(menu_id: str) -> str:
    existing = {b["id"] for b in _menu(menu_id)["buttons"]}
    i = 1
    while f"btn{i}" in existing:
        i += 1
    return f"btn{i}"


def _list_text(menu_id: str) -> str:
    buttons = sorted(_menu(menu_id)["buttons"], key=lambda b: (b.get("row", 1), b.get("position", 0)))
    if not buttons:
        return f"🔗 <b>Кнопки меню «{menu_id}»</b>\n{separator()}\nПока нет ни одной кнопки."
    lines = [f"🔗 <b>Кнопки меню «{menu_id}»</b>", separator()]
    for i, b in enumerate(buttons, start=1):
        icon = TYPE_ICONS.get(b["type"], "•")
        lines.append(f"{i}. {icon} {b['label']}  <i>(ряд {b.get('row', 1)})</i>")
    return "\n".join(lines)


def _ordered_buttons(menu_id: str) -> list[dict]:
    return sorted(_menu(menu_id)["buttons"], key=lambda b: (b.get("row", 1), b.get("position", 0)))


async def render_links_menu(bot, chat_id: int) -> None:
    keyboard = build_inline_keyboard(
        [
            {"id": "add", "label": "➕ Добавить кнопку", "type": "callback", "value": "links:add", "row": 1},
            {"id": "del", "label": "🗑 Удалить кнопку", "type": "callback", "value": "links:del", "row": 2},
            {"id": "move", "label": "🔀 Переместить кнопку", "type": "callback", "value": "links:move", "row": 3},
            {"id": "preview", "label": "👀 Предпросмотр меню", "type": "callback", "value": "links:preview", "row": 4},
            {"id": "back", "label": "⬅️ Назад", "type": "callback", "value": "admin:menu", "row": 5},
        ]
    )
    await show(bot, chat_id, {"type": "none", "text": _list_text(CURRENT_MENU_ID)}, keyboard)


@router.callback_query(F.data == "admin:links")
async def links_entry(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await render_links_menu(callback.bot, callback.message.chat.id)


@router.callback_query(F.data == "links:preview")
async def preview_menu(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    menu = _menu(CURRENT_MENU_ID)
    keyboard = build_inline_keyboard(menu["buttons"])
    text = "👀 Так меню выглядит у пользователя.\n\n" + menu["content"]["text"]
    back = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ Назад", "type": "callback", "value": "admin:links", "row": 99}]
    )
    keyboard.inline_keyboard += back.inline_keyboard
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


# ---------- Добавление кнопки ----------

@router.callback_query(F.data == "links:add")
async def add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ButtonStates.waiting_label)
    await state.update_data(menu_id=CURRENT_MENU_ID)
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:links", "row": 1}]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": "✏️ Пришлите текст (label) новой кнопки."}, keyboard)


@router.message(ButtonStates.waiting_label)
async def add_label(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(label=message.text, icon_custom_emoji_id=extract_custom_emoji_id(message))
    await state.set_state(ButtonStates.waiting_type)

    type_buttons = [
        {"id": f"t_{k}", "label": v, "type": "callback", "value": f"links:type:{k}", "row": i + 1}
        for i, (k, v) in enumerate(TYPE_LABELS.items())
    ]
    keyboard = build_inline_keyboard(type_buttons)
    await show(message.bot, message.chat.id, {"type": "none", "text": "📎 Выберите тип кнопки."}, keyboard)


@router.callback_query(ButtonStates.waiting_type, F.data.startswith("links:type:"))
async def add_type(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    btype = callback.data.split(":", 2)[2]
    await state.update_data(type=btype)
    await callback.answer()

    if btype == "generated":
        await state.set_state(ButtonStates.waiting_chat_source)
        text = (
            "📡 Пришлите источник чата/канала: перешлите любое сообщение из "
            "него, либо пришлите публичную ссылку t.me/..., @username или "
            "числовой chat_id (для приватных чатов — только форвардом)."
        )
    elif btype == "menu":
        await state.set_state(ButtonStates.waiting_menu_target)
        text = "📂 На какое меню должна вести кнопка? Пришлите id меню (например main или новое имя — оно будет создано)."
    elif btype == "copy":
        await state.set_state(ButtonStates.waiting_target)
        text = "📋 Пришлите текст, который будет копироваться при нажатии."
    elif btype == "share":
        await state.set_state(ButtonStates.waiting_target)
        text = "📤 Пришлите текст для предзаполнения при «поделиться», либо «-», если не нужен."
    elif btype == "callback":
        await state.set_state(ButtonStates.waiting_target)
        text = "🔘 Пришлите callback_data для этой кнопки (произвольный короткий код)."
    else:  # url / webapp
        await state.set_state(ButtonStates.waiting_target)
        text = "🌐 Пришлите ссылку (https://...)."

    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:links", "row": 1}]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(ButtonStates.waiting_target)
async def add_target(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    value = None if (message.text or "").strip() == "-" else message.text
    await state.update_data(value=value)
    await _ask_row(message, state)


@router.message(ButtonStates.waiting_menu_target)
async def add_menu_target(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    target_menu_id = message.text.strip()
    _menu(target_menu_id)  # создаст меню, если его ещё нет
    await state.update_data(value=target_menu_id)
    await _ask_row(message, state)


@router.message(ButtonStates.waiting_chat_source)
async def add_chat_source(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    chat_id = parse_chat_source(message)
    if not chat_id:
        await message.answer("❌ Не удалось распознать чат. Пришлите ссылку, @username, chat_id или перешлите сообщение из чата.")
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(ButtonStates.waiting_invite_mode)

    settings = load(SETTINGS_FILE)
    default_mode = settings.get("default_invite_mode", "request")
    keyboard = build_inline_keyboard(
        [
            {"id": "m_request", "label": "📝 По заявке", "type": "callback", "value": "links:mode:request", "row": 1},
            {"id": "m_single", "label": "1️⃣ Лимит на 1 человека", "type": "callback", "value": "links:mode:single_use", "row": 2},
            {"id": "m_default", "label": f"⚙️ Как по умолчанию ({default_mode})", "type": "callback", "value": "links:mode:default", "row": 3},
        ]
    )
    await show(message.bot, message.chat.id, {"type": "none", "text": f"✅ Чат распознан: <code>{chat_id}</code>\n\nВыберите режим ссылки."}, keyboard)


@router.callback_query(ButtonStates.waiting_invite_mode, F.data.startswith("links:mode:"))
async def add_invite_mode(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    mode = callback.data.split(":", 2)[2]
    if mode == "default":
        mode = load(SETTINGS_FILE).get("default_invite_mode", "request")
    await state.update_data(invite_mode=mode)
    await callback.answer()
    await _ask_row(callback, state, is_callback=True)


async def _ask_row(event, state: FSMContext, is_callback: bool = False) -> None:
    await state.set_state(ButtonStates.waiting_row)
    text = "🔢 В каком ряду разместить кнопку? Пришлите номер ряда (1, 2, 3...)."
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:links", "row": 1}]
    )
    bot = event.bot
    chat_id = event.message.chat.id if is_callback else event.chat.id
    await show(bot, chat_id, {"type": "none", "text": text}, keyboard)


@router.message(ButtonStates.waiting_row)
async def add_row(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Пришлите целое число, например 1.")
        return
    await state.update_data(row=int(message.text.strip()))
    await state.set_state(ButtonStates.waiting_icon)
    keyboard = build_inline_keyboard(
        [{"id": "skip", "label": "⏭ Пропустить", "type": "callback", "value": "links:icon:skip", "row": 1}]
    )
    text = "✨ Хотите добавить Premium/Custom Emoji перед текстом кнопки? Пришлите его сообщением, либо нажмите «Пропустить»."
    await show(message.bot, message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(ButtonStates.waiting_icon)
async def add_icon(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    icon_id = extract_custom_emoji_id(message)
    await state.update_data(icon_custom_emoji_id=icon_id)
    await _finalize_button(message.bot, message.chat.id, state)


@router.callback_query(ButtonStates.waiting_icon, F.data == "links:icon:skip")
async def add_icon_skip(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _finalize_button(callback.bot, callback.message.chat.id, state)


async def _finalize_button(bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    menu_id = data["menu_id"]
    menu = _menu(menu_id)
    row = data["row"]
    position = sum(1 for b in menu["buttons"] if b.get("row") == row) + 1

    new_button = {
        "id": _next_id(menu_id),
        "label": data["label"],
        "type": data["type"],
        "value": data.get("value"),
        "row": row,
        "position": position,
        "style": None,
        "icon_custom_emoji_id": data.get("icon_custom_emoji_id"),
    }
    if data["type"] == "generated":
        new_button["chat_id"] = data["chat_id"]
        new_button["invite_mode"] = data.get("invite_mode", "request")
        new_button["duration_minutes"] = load(SETTINGS_FILE).get("default_duration_minutes", 60)

    menu["buttons"].append(new_button)
    _save_links()
    await state.clear()

    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ К списку кнопок", "type": "callback", "value": "admin:links", "row": 1}]
    )
    await show(bot, chat_id, {"type": "none", "text": f"✅ Кнопка «{new_button['label']}» добавлена."}, keyboard)


# ---------- Удаление кнопки ----------

@router.callback_query(F.data == "links:del")
async def del_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    buttons = _ordered_buttons(CURRENT_MENU_ID)
    if not buttons:
        await callback.answer("Список пуст", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ButtonStates.waiting_delete_number)
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:links", "row": 1}]
    )
    text = _list_text(CURRENT_MENU_ID) + f"\n\n{separator()}\nПришлите номер кнопки для удаления."
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(ButtonStates.waiting_delete_number)
async def del_apply(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    buttons = _ordered_buttons(CURRENT_MENU_ID)
    if not message.text or not message.text.strip().isdigit() or not (1 <= int(message.text.strip()) <= len(buttons)):
        await message.answer(f"Пришлите номер от 1 до {len(buttons)}.")
        return
    target = buttons[int(message.text.strip()) - 1]
    menu = _menu(CURRENT_MENU_ID)
    menu["buttons"] = [b for b in menu["buttons"] if b["id"] != target["id"]]
    _save_links()
    await state.clear()
    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ К списку кнопок", "type": "callback", "value": "admin:links", "row": 1}]
    )
    await show(message.bot, message.chat.id, {"type": "none", "text": f"🗑 Кнопка «{target['label']}» удалена."}, keyboard)


# ---------- Перемещение кнопки ----------

@router.callback_query(F.data == "links:move")
async def move_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    buttons = _ordered_buttons(CURRENT_MENU_ID)
    if not buttons:
        await callback.answer("Список пуст", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ButtonStates.waiting_move_number)
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": "admin:links", "row": 1}]
    )
    text = _list_text(CURRENT_MENU_ID) + f"\n\n{separator()}\nПришлите номер кнопки, которую нужно переместить."
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(ButtonStates.waiting_move_number)
async def move_pick(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    buttons = _ordered_buttons(CURRENT_MENU_ID)
    if not message.text or not message.text.strip().isdigit() or not (1 <= int(message.text.strip()) <= len(buttons)):
        await message.answer(f"Пришлите номер от 1 до {len(buttons)}.")
        return
    target = buttons[int(message.text.strip()) - 1]
    await state.update_data(move_button_id=target["id"])
    await state.set_state(ButtonStates.waiting_move_target)
    text = (
        f"🔀 Куда переместить «{target['label']}»?\n"
        "Пришлите координаты в формате: <code>ряд позиция</code>, например «2 1» — "
        "это значит: ряд 2, первая слева. Можно указать только ряд («2») — "
        "тогда кнопка встанет последней в этом ряду."
    )
    await message.answer(text)


@router.message(ButtonStates.waiting_move_target)
async def move_apply(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if not parts or not parts[0].isdigit():
        await message.answer("Пришлите хотя бы номер ряда, например «2».")
        return

    new_row = int(parts[0])
    new_pos = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

    data = await state.get_data()
    menu = _menu(CURRENT_MENU_ID)
    target = next(b for b in menu["buttons"] if b["id"] == data["move_button_id"])

    # убираем из старого ряда и пересчитываем позиции соседей
    old_row = target.get("row", 1)
    same_old_row = [b for b in menu["buttons"] if b.get("row") == old_row and b["id"] != target["id"]]
    for i, b in enumerate(sorted(same_old_row, key=lambda x: x.get("position", 0)), start=1):
        b["position"] = i

    target["row"] = new_row
    same_new_row = [b for b in menu["buttons"] if b.get("row") == new_row and b["id"] != target["id"]]
    same_new_row.sort(key=lambda x: x.get("position", 0))

    insert_at = (new_pos - 1) if new_pos else len(same_new_row)
    insert_at = max(0, min(insert_at, len(same_new_row)))
    same_new_row.insert(insert_at, target)
    for i, b in enumerate(same_new_row, start=1):
        b["position"] = i

    _save_links()
    await state.clear()
    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ К списку кнопок", "type": "callback", "value": "admin:links", "row": 1}]
    )
    await show(message.bot, message.chat.id, {"type": "none", "text": f"✅ «{target['label']}» перемещена."}, keyboard)
