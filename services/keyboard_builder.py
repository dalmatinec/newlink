from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

# button = {
#   "id": str, "label": str, "type": "url|generated|menu|callback|webapp|copy|share",
#   "value": str | None,     # url / callback_data / menu_id / текст для copy / prefill для share
#   "row": int, "position": int,
#   "style": "primary|success|danger" | None,          # Bot API 9.4+
#   "icon_custom_emoji_id": str | None,                 # требует Fragment-юзернейм или Premium у владельца
# }


def build_inline_keyboard(buttons: list[dict]) -> InlineKeyboardMarkup:
    rows: dict[int, list[dict]] = {}
    for button in buttons:
        row_num = button.get("row", 1)
        rows.setdefault(row_num, []).append(button)

    keyboard: list[list[InlineKeyboardButton]] = []
    for row_num in sorted(rows.keys()):
        row_buttons = sorted(rows[row_num], key=lambda b: b.get("position", 0))
        line: list[InlineKeyboardButton] = []
        for button in row_buttons:
            line.append(_build_button(button))
        keyboard.append(line)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_button(button: dict) -> InlineKeyboardButton:
    kwargs = {"text": button["label"]}

    if button.get("style"):
        kwargs["style"] = button["style"]
    if button.get("icon_custom_emoji_id"):
        kwargs["icon_custom_emoji_id"] = button["icon_custom_emoji_id"]

    btype = button.get("type")
    value = button.get("value")

    if btype == "url":
        kwargs["url"] = value
    elif btype == "generated":
        kwargs["callback_data"] = f"link:{button['id']}"
    elif btype == "menu":
        kwargs["callback_data"] = f"menu:{value}"
    elif btype == "callback":
        kwargs["callback_data"] = value
    elif btype == "webapp":
        kwargs["web_app"] = WebAppInfo(url=value)
    elif btype == "copy":
        kwargs["copy_text"] = CopyTextButton(text=value)
    elif btype == "share":
        kwargs["switch_inline_query"] = value or ""
    else:
        # неизвестный тип — не даём боту упасть, просто не кликабельная кнопка на callback-заглушку
        kwargs["callback_data"] = "noop"

    return InlineKeyboardButton(**kwargs)
