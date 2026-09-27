"""Админка: тексты экранов и системные кнопки, разложенные по разделам."""
from html import escape

from aiogram.types import Message

from ... import premium
from .core import (
    Ctx, InputError, Rows, ViewResult, action, b, back_btn, label_info, label_rows, media_line, on_input, preview,
    rich_rows, view,
)

USER = {"имя": "имя", "полное_имя": "имя и фамилия", "юзернейм": "@юзернейм (если нет, то имя)",
        "упоминание": "имя со ссылкой на профиль", "id": "Telegram ID"}

# ключ -> (название, где показывается, доп. плейсхолдеры, можно ли картинку)
TEXTS: dict[str, tuple[str, str, dict[str, str], bool]] = {
    "start": ("Приветствие", "Главный экран после /start, над кнопками.", {}, True),
    "empty_menu": ("Меню пустое", "Добавляется к приветствию, пока нет ни одной кнопки.", {}, False),
    "link_one_time": ("Одноразовая ссылка", "Экран с кнопкой Вступить для одноразовых ссылок.",
                      {"кнопка": "название нажатой кнопки"}, True),
    "link_request": ("Ссылка по заявке", "Экран с кнопкой Вступить для ссылок по заявке.",
                     {"кнопка": "название нажатой кнопки"}, True),
    "link_ttl": ("Строка про срок", "Добавляется к экрану ссылки, если у кнопки есть срок.",
                 {"время": "сколько осталось"}, False),
    "sponsors": ("Подпишись на спонсоров", "Экран перед выдачей ссылки, пока юзер не подписан.",
                 {"кнопка": "название нажатой кнопки"}, True),
    "sponsors_missing": ("Ещё не подписался", "Всплывает после Я подписался, если подписки нет.", {}, False),
    "unavailable": ("Раздел недоступен", "Кнопка выключена или её чат недоступен.", {}, False),
    "busy": ("Много желающих", "Telegram попросил подождать с созданием ссылок.", {}, False),
    "banned": ("Сообщение забаненному", "Всплывает у забаненного пользователя.", {}, False),
    "flood": ("Слишком быстро", "Всплывает, если человек жмёт слишком часто.", {}, False),
}
POPUPS = {"sponsors_missing", "unavailable", "busy", "banned", "flood"}

# разделы: ключ -> (название, тексты)
GROUPS: dict[str, tuple[str, list[str]]] = {
    "home": ("🏠 Главный экран", ["start", "empty_menu"]),
    "link": ("🔗 Выдача ссылки", ["link_one_time", "link_request", "link_ttl"]),
    "sp": ("🤝 Спонсоры", ["sponsors", "sponsors_missing"]),
    "pop": ("💬 Всплывающие сообщения", ["unavailable", "busy", "banned", "flood"]),
}

BUTTONS = {
    "join": ("Вступить", "Кнопка со ссылкой на экране выдачи."),
    "back": ("Назад", "Возврат в главное меню."),
    "check_subs": ("Я подписался", "На экране спонсоров: проверить подписку ещё раз."),
    "sponsor": ("Кнопки спонсоров", "Иконка и цвет кнопок спонсоров. Текст у каждого спонсора свой."),
}


def group_of(key: str) -> str:
    return next((g for g, (_, keys) in GROUPS.items() if key in keys), "home")


@view("texts", "texts")
async def view_texts(ctx: Ctx) -> ViewResult:
    rows: Rows = [[b(title, f"a:txg:{g}")] for g, (title, _) in GROUPS.items()]
    rows.append([b("🔘 Кнопки бота", "a:btns")])
    rows.append([b("✨ Проверить премиум-эмодзи", "x:prem")])
    rows.append(back_btn("a:cfg"))
    return ("📝 <b>Тексты и кнопки</b>\n\nВсё, что видит пользователь. Выбери раздел.\n"
            "Можно писать с форматированием и премиум-эмодзи."), rows


@view("txg", "texts")
async def view_text_group(ctx: Ctx, group: str) -> ViewResult:
    title, keys = GROUPS.get(group, GROUPS["home"])
    rows: Rows = [[b(TEXTS[k][0], f"a:text:{k}")] for k in keys]
    rows.append(back_btn("a:texts"))
    hint = "Это всплывающие окна: короткий текст, без картинки." if group == "pop" else "Выбери текст."
    return f"{title}\n\n{hint}", rows


@view("text", "texts")
async def view_text(ctx: Ctx, key: str) -> ViewResult:
    if key not in TEXTS:
        return await view_texts(ctx)
    name, where, extra, rich = TEXTS[key]
    row = await ctx.app.db.fetchone("SELECT * FROM texts WHERE key = ?", (key,))
    placeholders = {**(USER if key not in POPUPS else {}), **extra}
    help_ = "\n".join(f"<code>{{{k}}}</code>  {v}" for k, v in placeholders.items())
    html = f"📝 <b>{escape(name)}</b>\n<i>{escape(where)}</i>\n\n<b>Сейчас:</b>\n<blockquote>{preview(row['html'] if row else '')}</blockquote>"
    if rich and row:
        html += f"\nКартинка: {media_line(ctx.app, row['media_id'])}"
    if help_:
        html += f"\n\n<b>Можно подставить:</b>\n{help_}"
    if key in POPUPS:
        html += "\n\nВсплывающее окно: только текст, до 190 символов."
    rows = rich_rows("text", key, row) if rich and row else [[b("📝 Изменить текст", f"x:htm:text:{key}")]]
    rows.append(back_btn(f"a:txg:{group_of(key)}"))
    return html, rows


@view("btns", "texts")
async def view_buttons(ctx: Ctx) -> ViewResult:
    store = ctx.app.store
    rows: Rows = []
    for k, (name, _) in BUTTONS.items():
        btn = store.button(k)
        rows.append([b(f"{name}: {btn.label}", f"a:btn:{k}", btn.style, btn.icon)])
    rows.append(back_btn("a:texts"))
    return "🔘 <b>Кнопки бота</b>\nПоказаны так, как их видят юзеры. Можно поменять текст, иконку и цвет.", rows


@view("btn", "texts")
async def view_button(ctx: Ctx, key: str) -> ViewResult:
    if key not in BUTTONS:
        return await view_buttons(ctx)
    row = await ctx.app.db.fetchone("SELECT * FROM buttons WHERE key = ?", (key,))
    name, where = BUTTONS[key]
    html = (f"🔘 <b>{escape(name)}</b>\n<i>{escape(where)}</i>\n\n"
            f"Текст: {escape(row['label'])}\n{label_info(row)}")
    rows = label_rows("btn", key, row)
    rows.append(back_btn("a:btns"))
    return html, rows


@action("prem", "texts")
async def act_premium_check(ctx: Ctx):
    return await ctx.ask("prem", "✨ Пришли любое <b>премиум-эмодзи</b>. Бот проверит, показывает ли Telegram "
                                 "премиум-эмодзи от этого бота в тексте и на кнопках.", "a:texts")


@on_input("prem", "texts")
async def in_premium_check(ctx: Ctx, message: Message):
    ids = premium.custom_emoji_ids(message)
    if not ids:
        raise InputError("В сообщении нет премиум-эмодзи. Выбери эмодзи из премиум-набора.")
    result = await premium.check(ctx.app.bot, ctx.chat_id, ids[0], force=True)
    ctx.notice = premium.describe(result)
    return "a:texts"
