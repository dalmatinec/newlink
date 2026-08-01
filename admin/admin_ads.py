from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin.admin_menu import is_admin
from services.content_extractor import extract_content
from services.json_store import load, save
from services.keyboard_builder import build_inline_keyboard
from services.navigator import show
from states import AdStates
from text_format import separator

router = Router(name="admin_ads")

ADS_FILE = "ads.json"

SCHEDULE_LABELS = {
    "every_start": "🔁 Каждый запуск",
    "once_ever": "1️⃣ Один раз за всё время",
    "interval_hours": "⏱ Раз в N часов",
    "times_per_day": "📅 N раз в сутки",
}


def _slots() -> dict:
    return load(ADS_FILE)["slots"]


def _save_ads() -> None:
    save(ADS_FILE, load(ADS_FILE))


def _slot_status_line(slot_id: str, slot: dict) -> str:
    state = "🟢" if slot.get("enabled") else "⚪️"
    mode = slot.get("schedule", {}).get("mode", "every_start")
    return f"{state} Слот {slot_id} — {SCHEDULE_LABELS.get(mode, mode)}"


async def render_ads_menu(bot, chat_id: int) -> None:
    slots = _slots()
    lines = ["📣 <b>Реклама</b>", separator(), "Нажмите на слот ниже, чтобы настроить."]
    text = "\n".join(lines)
    buttons = []
    for i, (slot_id, slot) in enumerate(slots.items(), start=1):
        buttons.append(
            {"id": f"ad_{slot_id}", "label": _slot_status_line(slot_id, slot), "type": "callback", "value": f"ads:slot:{slot_id}", "row": i}
        )
    buttons.append({"id": "back", "label": "⬅️ Назад", "type": "callback", "value": "admin:menu", "row": 99})
    keyboard = build_inline_keyboard(buttons)
    await show(bot, chat_id, {"type": "none", "text": text}, keyboard)


@router.callback_query(F.data == "admin:ads")
async def ads_entry(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await render_ads_menu(callback.bot, callback.message.chat.id)


@router.callback_query(F.data.startswith("ads:slot:"))
async def slot_detail(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    slot_id = callback.data.split(":", 2)[2]
    await state.update_data(slot_id=slot_id)
    await callback.answer()

    slot = _slots()[slot_id]
    has_content = bool(slot.get("content"))
    text = (
        f"📣 <b>Слот {slot_id}</b>\n"
        f"{separator()}\n"
        f"Статус: {'🟢 включен' if slot.get('enabled') else '⚪️ выключен'}\n"
        f"Контент: {'✅ задан' if has_content else '❌ не задан'}\n"
        f"Расписание: {SCHEDULE_LABELS.get(slot['schedule']['mode'], slot['schedule']['mode'])}"
    )
    keyboard = build_inline_keyboard(
        [
            {"id": "set_content", "label": "✏️ Задать контент", "type": "callback", "value": f"ads:content:{slot_id}", "row": 1},
            {"id": "set_schedule", "label": "🗓 Расписание показа", "type": "callback", "value": f"ads:schedule:{slot_id}", "row": 2},
            {"id": "toggle", "label": "🔁 Вкл/выкл слот", "type": "callback", "value": f"ads:toggle:{slot_id}", "row": 3},
            {"id": "clear", "label": "🗑 Очистить слот", "type": "callback", "value": f"ads:clear:{slot_id}", "row": 4},
            {"id": "back", "label": "⬅️ Назад", "type": "callback", "value": "admin:ads", "row": 5},
        ]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.callback_query(F.data.startswith("ads:toggle:"))
async def toggle_slot(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    slot_id = callback.data.split(":", 2)[2]
    slot = _slots()[slot_id]
    if not slot.get("content"):
        await callback.answer("Сначала задайте контент слота", show_alert=True)
        return
    slot["enabled"] = not slot.get("enabled", False)
    _save_ads()
    await callback.answer("Готово")
    await slot_detail(callback, state)


@router.callback_query(F.data.startswith("ads:clear:"))
async def clear_slot(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    slot_id = callback.data.split(":", 2)[2]
    slot = _slots()[slot_id]
    slot["content"] = None
    slot["buttons"] = []
    slot["enabled"] = False
    _save_ads()
    await callback.answer("Слот очищен")
    await slot_detail(callback, state)


# ---------- Контент слота: форвард или вручную ----------

@router.callback_query(F.data.startswith("ads:content:"))
async def content_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    slot_id = callback.data.split(":", 2)[2]
    await state.update_data(slot_id=slot_id)
    await state.set_state(AdStates.waiting_content)
    await callback.answer()

    text = (
        "✏️ Пришлите рекламное сообщение одним из способов:\n"
        f"{separator()}\n"
        "• перешлите готовый пост (форвард) — сохранится как есть;\n"
        "• или пришлите текст/фото/видео/GIF с подписью вручную."
    )
    keyboard = build_inline_keyboard(
        [{"id": "cancel", "label": "❌ Отмена", "type": "callback", "value": f"ads:slot:{slot_id}", "row": 1}]
    )
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": text}, keyboard)


@router.message(AdStates.waiting_content)
async def content_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    slot_id = data["slot_id"]
    content = extract_content(message)
    slot = _slots()[slot_id]
    slot["content"] = content
    _save_ads()
    await state.clear()

    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ К слоту", "type": "callback", "value": f"ads:slot:{slot_id}", "row": 1}]
    )
    await show(message.bot, message.chat.id, {"type": "none", "text": "✅ Контент слота сохранён."}, keyboard)


# ---------- Расписание ----------

@router.callback_query(F.data.startswith("ads:schedule:"))
async def schedule_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    slot_id = callback.data.split(":", 2)[2]
    await state.update_data(slot_id=slot_id)
    await callback.answer()

    buttons = [
        {"id": f"m_{k}", "label": v, "type": "callback", "value": f"ads:mode:{slot_id}:{k}", "row": i + 1}
        for i, (k, v) in enumerate(SCHEDULE_LABELS.items())
    ]
    keyboard = build_inline_keyboard(buttons)
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": "🗓 Выберите режим показа."}, keyboard)


@router.callback_query(F.data.startswith("ads:mode:"))
async def schedule_mode_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    _, _, slot_id, mode = callback.data.split(":", 3)
    slot = _slots()[slot_id]
    await callback.answer()

    if mode in ("every_start", "once_ever"):
        slot["schedule"]["mode"] = mode
        _save_ads()
        keyboard = build_inline_keyboard(
            [{"id": "back", "label": "⬅️ К слоту", "type": "callback", "value": f"ads:slot:{slot_id}", "row": 1}]
        )
        await show(callback.bot, callback.message.chat.id, {"type": "none", "text": "✅ Расписание обновлено."}, keyboard)
        return

    await state.update_data(slot_id=slot_id, schedule_mode=mode)
    await state.set_state(AdStates.waiting_schedule_value)
    prompt = "⏱ Пришлите число часов между показами (например 12)." if mode == "interval_hours" else "📅 Пришлите, сколько раз в сутки показывать (например 2)."
    await show(callback.bot, callback.message.chat.id, {"type": "none", "text": prompt}, None)


@router.message(AdStates.waiting_schedule_value)
async def schedule_value_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Пришлите целое число.")
        return
    value = int(message.text.strip())
    data = await state.get_data()
    slot_id = data["slot_id"]
    mode = data["schedule_mode"]
    slot = _slots()[slot_id]
    slot["schedule"]["mode"] = mode
    if mode == "interval_hours":
        slot["schedule"]["interval_hours"] = value
    else:
        slot["schedule"]["times_per_day"] = value
    _save_ads()
    await state.clear()

    keyboard = build_inline_keyboard(
        [{"id": "back", "label": "⬅️ К слоту", "type": "callback", "value": f"ads:slot:{slot_id}", "row": 1}]
    )
    await show(message.bot, message.chat.id, {"type": "none", "text": "✅ Расписание обновлено."}, keyboard)
