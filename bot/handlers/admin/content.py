"""Админка: тексты экранов и системные кнопки. Всё, что видит пользователь, меняется здесь."""
from html import escape

from .core import (
    Ctx, Rows, ViewResult, b, back_btn, icon_line, label_rows, media_line, rich_rows, snippet, style_name, view,
)

USER = {"имя": "имя", "полное_имя": "имя и фамилия", "юзернейм": "@юзернейм (если нет - имя)",
        "упоминание": "имя со ссылкой на профиль", "id": "Telegram ID"}

# ключ -> (название, где показывается, доп. плейсхолдеры, можно ли картинку)
TEXTS: dict[str, tuple[str, str, dict[str, str], bool]] = {
    "start": ("🏠 Приветствие и меню", "Главный экран после /start, над кнопками.", {}, True),
    "empty_menu": ("📭 Меню пустое", "Добавляется к приветствию, пока нет ни одной кнопки.", {}, False),
    "link_one_time": ("🔂 Ссылка готова (одноразовая)", "Экран с кнопкой Вступить для одноразовых ссылок.",
                      {"кнопка": "название нажатой кнопки"}, True),
    "link_request": ("📨 Ссылка готова (по заявке)", "Экран с кнопкой Вступить для ссылок по заявке.",
                     {"кнопка": "название нажатой кнопки"}, True),
    "link_ttl": ("⏳ Строка про срок ссылки", "Добавляется к экрану ссылки, если у кнопки есть срок.",
                 {"время": "сколько осталось"}, False),
    "sponsors": ("🤝 Подпишись на спонсоров", "Экран перед выдачей ссылки, пока юзер не подписан.",
                 {"кнопка": "название нажатой кнопки"}, True),
    "sponsors_missing": ("🙈 Не подписался (всплывашка)", "Всплывает после Я подписался, если подписки нет.", {}, False),
    "unavailable": ("⏳ Раздел недоступен (всплывашка)", "Кнопка выключена или её чат недоступен.", {}, False),
    "busy": ("🙏 Слишком много желающих (всплывашка)", "Telegram попросил подождать с созданием ссылок.", {}, False),
    "banned": ("🚫 Сообщение забаненному", "Всплывает у забаненного пользователя.", {}, False),
    "flood": ("🐢 Слишком быстро (антифлуд)", "Всплывает, если человек жмёт слишком часто.", {}, False),
}
POPUPS = {"sponsors_missing", "unavailable", "busy", "banned", "flood"}

BUTTONS = {
    "join": ("🚀 Вступить", "Кнопка со ссылкой на экране выдачи."),
    "back": ("◀️ Назад", "Возврат в главное меню."),
    "check_subs": ("✅ Я подписался", "На экране спонсоров: проверить подписку ещё раз."),
    "sponsor": ("➕ Подписаться", "Иконка и цвет кнопок спонсоров (текст - у каждого спонсора свой)."),
}


@view("texts", "texts")
async def view_texts(ctx: Ctx) -> ViewResult:
    rows: Rows = [[b(name, f"a:text:{key}")] for key, (name, _, _, _) in TEXTS.items()]
    rows.append([b("🔘 Системные кнопки", "a:btns")])
    rows.append(back_btn("a:cfg"))
    return ("📝 <b>Тексты и кнопки</b>\n\nВсё, что видит пользователь. Можно с форматированием и премиум-эмодзи, "
            "у главных экранов - с картинкой, GIF или видео."), rows


@view("text", "texts")
async def view_text(ctx: Ctx, key: str) -> ViewResult:
    if key not in TEXTS:
        return await view_texts(ctx)
    name, where, extra, rich = TEXTS[key]
    row = await ctx.app.db.fetchone("SELECT * FROM texts WHERE key = ?", (key,))
    placeholders = {**(USER if key not in POPUPS else {}), **extra}
    help_ = "\n".join(f"<code>{{{k}}}</code> - {v}" for k, v in placeholders.items())
    html = (f"{name}\n<i>{escape(where)}</i>\n\n"
            f"<b>Сейчас:</b>\n<blockquote>{snippet(row['html'] if row else '', 600)}</blockquote>\n"
            + (f"Картинка: {media_line(ctx.app, row['media_id'])}\n" if rich and row else "")
            + (f"\n<b>Можно подставить:</b>\n{help_}" if help_ else "")
            + ("\n\nЭто всплывающее окно: только текст, до 190 символов." if key in POPUPS else ""))
    if rich and row:
        rows = rich_rows("text", key, row)
    else:
        rows = [[b("📝 Изменить текст", f"x:htm:text:{key}")]]
    rows.append(back_btn("a:texts"))
    return html, rows


@view("btns", "texts")
async def view_buttons(ctx: Ctx) -> ViewResult:
    store = ctx.app.store
    rows: Rows = [[b(f"{store.button(k).label} · {name.split(' ', 1)[1]}", f"a:btn:{k}")] for k, (name, _) in BUTTONS.items()]
    rows.append(back_btn("a:texts"))
    return "🔘 <b>Системные кнопки</b>\nТекст, премиум-иконка и цвет.", rows


@view("btn", "texts")
async def view_button(ctx: Ctx, key: str) -> ViewResult:
    if key not in BUTTONS:
        return await view_buttons(ctx)
    row = await ctx.app.db.fetchone("SELECT * FROM buttons WHERE key = ?", (key,))
    name, where = BUTTONS[key]
    html = (f"🔘 <b>{escape(row['label'])}</b>\n<i>{escape(where)}</i>\n\n"
            f"Иконка: {icon_line(row['icon'])}\nЦвет: {style_name(row['style'])}")
    rows = label_rows("btn", key, row)
    rows.append(back_btn("a:btns"))
    return html, rows
