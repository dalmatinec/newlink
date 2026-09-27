"""Админка: кнопки меню (личные ссылки в чаты и обычные ссылки), привязка и замена чатов."""
from html import escape

from aiogram.types import Message

from ...richtext import normalize_url, parse_contacts, parse_label
from ...store import Item, now
from ...ui import button, minutes_text
from .core import (
    Ctx, InputError, Rows, ViewResult, action, b, back_btn, label_info, label_rows, move, on_input, view, yes_no,
)

MODES = {"one_time": "🔂 Одноразовая", "request": "📨 По заявке"}
MODE_HELP = {
    "one_time": "ссылка на 1 вход, сгорает после использования",
    "request": "человек подаёт заявку, бот одобряет только владельца ссылки",
}
TTL_PRESETS = [0, 15, 60, 360, 1440, 10080]


def status_icon(ctx: Ctx, item: Item) -> str:
    if not item.is_active:
        return "⏸"
    if item.kind == "url":
        return "🌐"
    if item.chat_id is None:
        return "🔌"
    return "✅" if ctx.app.store.item_ready(item) else "⚠️"


def chat_title(ctx: Ctx, chat_id: int | None) -> str:
    chat = ctx.app.store.chats.get(chat_id or 0)
    if chat is None:
        return "не привязан"
    return f"{escape(chat.title)} ({chat.kind_name})"


def item_or_none(ctx: Ctx, item_id: str) -> Item | None:
    return ctx.app.store.items.get(int(item_id)) if item_id.isdigit() else None


# ---------- список ----------
def title_html(item: Item) -> str:
    icon = f'<tg-emoji emoji-id="{item.icon}">⭐️</tg-emoji> ' if item.icon else ""
    return f"{icon}<b>{escape(item.label)}</b>"


@view("items", "links")
async def view_items(ctx: Ctx) -> ViewResult:
    store = ctx.app.store
    items = list(store.items.values())
    # кнопки показаны так, как их видят юзеры: с иконкой и цветом
    rows: Rows = [[b(f"{status_icon(ctx, i)} {i.label}", f"a:item:{i.id}", i.style, i.icon)] for i in items]
    rows.append([b("➕ Ссылка в чат", "x:inew", "success"), b("🌐 Обычная ссылка", "x:iunew")])
    if len(items) > 1:
        rows.append([b("↕️ Порядок кнопок", "a:iord"), b(f"▦ В ряду: {store.setting('per_row', 2)}", "x:perrow")])
    else:
        rows.append([b(f"▦ В ряду: {store.setting('per_row', 2)}", "x:perrow")])
    rows.append([b("🔌 Чаты бота", "a:chats")])
    rows.append(back_btn("a:home"))
    html = ("🔗 <b>Кнопки меню</b>\n\n"
            "✅ работает   ⚠️ чат недоступен   🔌 нет чата\n🌐 обычная ссылка   ⏸ выключена\n\n"
            + ("Нажми на кнопку, чтобы настроить." if items else "Кнопок пока нет. Создай первую."))
    return html, rows


@action("perrow", "links")
async def act_per_row(ctx: Ctx):
    cur = int(ctx.app.store.setting("per_row", 2))
    await ctx.app.set_setting("per_row", cur % 3 + 1)
    return "a:items"


@view("iord", "links")
async def view_order(ctx: Ctx) -> ViewResult:
    rows: Rows = [[b(i.label, f"a:item:{i.id}", i.style, i.icon), b("⬆️", f"x:iord:{i.id}:-1"), b("⬇️", f"x:iord:{i.id}:1")]
                  for i in ctx.app.store.items.values()]
    rows.append(back_btn("a:items"))
    return "↕️ <b>Порядок кнопок</b>\nЖми ⬆️ и ⬇️, кнопки меняются местами сразу. Так же они стоят в меню у юзеров.", rows


@action("iord", "links")
async def act_order(ctx: Ctx, item_id: str, delta: str):
    await move(ctx, "items", int(item_id), int(delta))
    return "a:iord"


# ---------- карточка ----------
@view("item", "links")
async def view_item(ctx: Ctx, item_id: str) -> ViewResult:
    item = item_or_none(ctx, item_id)
    if item is None:
        return await view_items(ctx)
    app, db, t = ctx.app, ctx.app.db, now()
    k = item.id
    row = await _row(ctx, k)
    look = f"<b>Как выглядит</b>\n{label_info(row)}"
    if item.kind == "url":
        html = (f"🌐 {title_html(item)}\n{'✅ включена' if item.is_active else '⏸ выключена'}\n\n"
                f"Ссылка: {escape(item.url or 'нет')}\n\n{look}")
        rows = label_rows("item", str(k), row)
        rows.append([b("🌐 Изменить ссылку", f"x:iurl:{k}"), b(f"↔️ Широкая: {yes_no(item.wide)}", f"x:iwide:{k}")])
        rows += _common_rows(item)
        return html, rows

    issued_day = await db.fetchval("SELECT COUNT(*) FROM invite_links WHERE item_id = ? AND assigned_at > ?",
                                   (k, t - 86400))
    issued_month = await db.fetchval("SELECT COUNT(*) FROM invite_links WHERE item_id = ? AND assigned_at > ?",
                                     (k, t - 30 * 86400))
    joined_day = await db.fetchval("SELECT COUNT(*) FROM joins WHERE item_id = ? AND ts > ?", (k, t - 86400))
    joined_all = await db.fetchval("SELECT COUNT(*) FROM joins WHERE item_id = ?", (k,))
    ready = app.store.item_ready(item)
    if not item.is_active:
        status = "⏸ выключена, юзеры её не видят"
    elif item.chat_id is None:
        status = "🔌 чат не привязан, нажми 🔗 Привязать чат"
    elif not ready:
        status = "⚠️ чат недоступен, нажми 🔄 Заменить чат"
    else:
        status = "✅ работает"
    pool = app.links.pool_counts.get(k, 0)
    target = int(app.store.setting("pool_size", 5))
    html = (
        f"🔗 {title_html(item)}\n{status}\n\n"
        f"<b>Куда ведёт</b>\n"
        f"Чат: {chat_title(ctx, item.chat_id)}\n"
        f"Тип ссылки: {MODES[item.mode]} ({MODE_HELP[item.mode]})\n"
        f"Срок ссылки: {minutes_text(item.ttl_minutes)}\n"
        f"Спонсоры перед выдачей: {'нет' if item.skip_sponsors else 'да'}\n\n"
        f"{look}\n\n"
        f"<b>Статистика</b>\n"
        f"Выдано: сутки <b>{issued_day}</b>, 30 дней <b>{issued_month}</b>\n"
        f"Вступили: сутки <b>{joined_day}</b>, всего <b>{joined_all}</b>\n"
        + (f"Готовых ссылок в запасе: {pool} из {target}\n" if target and ready else "")
        + f"\n📎 Ссылка для постов:\n<code>https://t.me/{app.bot_username}?start=i{k}</code>"
    )
    rows = label_rows("item", str(k), row)
    rows.append([b("🔄 Заменить чат" if item.chat_id else "🔗 Привязать чат", f"a:bind:{k}",
                   "primary" if not ready else None)])
    rows.append([b(f"🔁 Тип: {MODES[item.mode]}", f"x:imode:{k}"),
                 b(f"⏱ Срок: {minutes_text(item.ttl_minutes)}", f"a:ittl:{k}")])
    rows.append([b(f"🤝 Спонсоры: {'нет' if item.skip_sponsors else 'да'}", f"x:isk:{k}"),
                 b(f"↔️ Широкая: {yes_no(item.wide)}", f"x:iwide:{k}")])
    rows += _common_rows(item, revoke=True)
    return html, rows


async def _row(ctx: Ctx, item_id: int):
    return await ctx.app.db.fetchone("SELECT * FROM items WHERE id = ?", (item_id,))


def _common_rows(item: Item, revoke: bool = False) -> Rows:
    k = item.id
    last = [b("🗑 Отозвать ссылки", f"a:irev:{k}")] if revoke else []
    return [
        [b("⬆️ Выше", f"x:imv:{k}:-1"), b("⬇️ Ниже", f"x:imv:{k}:1"),
         b("⏸ Выключить" if item.is_active else "▶️ Включить", f"x:ion:{k}")],
        last + [b("❌ Удалить", f"a:idel:{k}", "danger")],
        back_btn("a:items"),
    ]


# ---------- создание ----------
@action("inew", "links")
async def act_item_new(ctx: Ctx):
    return await ctx.ask("inew", "✏️ Название новой кнопки (можно с премиум-эмодзи - оно станет иконкой):", "a:items")


@on_input("inew", "links")
async def in_item_new(ctx: Ctx, message: Message):
    if not message.text:
        raise InputError("Нужен текст.")
    label, icon = parse_label(message)
    if not label:
        raise InputError("Пустое название.")
    item_id = await _create(ctx, "invite", label, icon)
    await ctx.log("item.create", label)
    ctx.app.start_bind(ctx.user_id, "item", item_id)
    return f"a:bind:{item_id}"


@action("iunew", "links")
async def act_url_new(ctx: Ctx):
    return await ctx.ask("iunew", "🌐 Пришлите кнопку одной строкой:\n<code>Текст кнопки | https://ссылка</code>\n"
                                  "Можно @username или t.me/... Премиум-эмодзи станет иконкой.", "a:items")


@on_input("iunew", "links")
async def in_url_new(ctx: Ctx, message: Message):
    buttons, errors = parse_contacts(message)
    if errors or len(buttons) != 1:
        raise InputError(errors[0] if errors else "Нужна одна строка Текст | ссылка.")
    label, url, icon = buttons[0]
    item_id = await _create(ctx, "url", label, icon, url)
    await ctx.log("item.create", label)
    ctx.notice = "✅ Кнопка добавлена в меню"
    return f"a:item:{item_id}"


async def _create(ctx: Ctx, kind: str, label: str, icon: str | None, url: str | None = None) -> int:
    pos = await ctx.app.db.fetchval("SELECT COALESCE(MAX(position), 0) + 1 FROM items")
    item_id = await ctx.app.db.execute(
        "INSERT INTO items(kind, label, icon, url, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, label, icon, url, pos, now()))
    await ctx.reload()
    return item_id


@action("iurl", "links")
async def act_item_url(ctx: Ctx, item_id: str):
    return await ctx.ask("iurl", "🌐 Пришлите новую ссылку (https://..., t.me/... или @username):", f"a:item:{item_id}",
                         item_id)


@on_input("iurl", "links")
async def in_item_url(ctx: Ctx, message: Message, item_id: str):
    url = normalize_url(message.text or "")
    if url is None:
        raise InputError("Это не похоже на ссылку.")
    await ctx.app.db.execute("UPDATE items SET url = ? WHERE id = ?", (url, int(item_id)))
    await ctx.reload()
    ctx.notice = "✅ Ссылка сохранена"
    return f"a:item:{item_id}"


# ---------- настройки кнопки ----------
async def _set(ctx: Ctx, item_id: str, **fields) -> str:
    cols = ", ".join(f"{c} = ?" for c in fields)
    await ctx.app.db.execute(f"UPDATE items SET {cols} WHERE id = ?", (*fields.values(), int(item_id)))
    await ctx.reload()
    ctx.app.links.wake.set()
    return f"a:item:{item_id}"


@action("imode", "links")
async def act_item_mode(ctx: Ctx, item_id: str):
    item = item_or_none(ctx, item_id)
    if item is None:
        return "a:items"
    new = "request" if item.mode == "one_time" else "one_time"
    await ctx.app.links.revoke("item_id = ? AND user_id IS NOT NULL", (item.id,))
    ctx.notice = f"✅ Режим: {MODES[new]}. Старые ссылки отозваны, запас пересоздаётся."
    await ctx.log("item.mode", f"{item.id}: {new}")
    return await _set(ctx, item_id, mode=new)


@view("ittl", "links")
async def view_item_ttl(ctx: Ctx, item_id: str) -> ViewResult:
    item = item_or_none(ctx, item_id)
    if item is None:
        return await view_items(ctx)
    btns = [b(("✅ " if item.ttl_minutes == m else "") + minutes_text(m), f"x:ittl:{item_id}:{m}") for m in TTL_PRESETS]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([b("✍️ Своё значение", f"x:ittlin:{item_id}")])
    rows.append(back_btn(f"a:item:{item_id}"))
    return ("⏱ <b>Срок жизни ссылки</b>\nСколько живёт выданная ссылка, если по ней не вошли. "
            "Потом бот её отзовёт, а человек при нажатии получит новую."), rows


@action("ittl", "links")
async def act_item_ttl(ctx: Ctx, item_id: str, minutes: str):
    return await _set(ctx, item_id, ttl_minutes=max(0, int(minutes)))


@action("ittlin", "links")
async def act_item_ttl_input(ctx: Ctx, item_id: str):
    return await ctx.ask("ittl", "Сколько минут живёт ссылка? Пришлите число (0 - без срока).", f"a:ittl:{item_id}",
                         item_id)


@on_input("ittl", "links")
async def in_item_ttl(ctx: Ctx, message: Message, item_id: str):
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) > 525600:
        raise InputError("Нужно число минут от 0 до 525600.")
    return await _set(ctx, item_id, ttl_minutes=int(text))


@action("isk", "links")
async def act_item_skip(ctx: Ctx, item_id: str):
    item = item_or_none(ctx, item_id)
    return await _set(ctx, item_id, skip_sponsors=int(not item.skip_sponsors)) if item else "a:items"


@action("iwide", "links")
async def act_item_wide(ctx: Ctx, item_id: str):
    item = item_or_none(ctx, item_id)
    return await _set(ctx, item_id, wide=int(not item.wide)) if item else "a:items"


@action("ion", "links")
async def act_item_on(ctx: Ctx, item_id: str):
    item = item_or_none(ctx, item_id)
    if item is None:
        return "a:items"
    await ctx.log("item.toggle", f"{item.id}")
    return await _set(ctx, item_id, is_active=int(not item.is_active))


@action("imv", "links")
async def act_item_move(ctx: Ctx, item_id: str, delta: str):
    await move(ctx, "items", int(item_id), int(delta))
    return f"a:item:{item_id}"


@view("irev", "links")
async def view_item_revoke(ctx: Ctx, item_id: str) -> ViewResult:
    n = await ctx.app.db.fetchval(
        "SELECT COUNT(*) FROM invite_links WHERE item_id = ? AND user_id IS NOT NULL AND revoked = 0 AND used_at IS NULL",
        (int(item_id),))
    if not n:
        ctx.notice = "Живых выданных ссылок нет."
        return await view_item(ctx, item_id)
    return (f"🗑 Отозвать <b>{n}</b> выданных неиспользованных ссылок?\n"
            "Они сразу перестанут работать. Кто нажмёт кнопку снова - получит новую.\n\n"
            "Telegram не даёт ботам стирать ссылки совсем: отозванные лежат в разделе Отозванные "
            "в настройках чата, их можно удалить оттуда одной кнопкой.",
            [[b("🗑 Отозвать", f"x:irev:{item_id}", "danger"), b("✖️ Отмена", f"a:item:{item_id}")]])


@action("irev", "links")
async def act_item_revoke(ctx: Ctx, item_id: str):
    n = await ctx.app.links.revoke("item_id = ? AND user_id IS NOT NULL", (int(item_id),))
    await ctx.log("item.revoke", f"{item_id}: {n}")
    ctx.notice = f"✅ Отозвано ссылок: {n}"
    return f"a:item:{item_id}"


@view("idel", "links")
async def view_item_delete(ctx: Ctx, item_id: str) -> ViewResult:
    item = item_or_none(ctx, item_id)
    if item is None:
        return await view_items(ctx)
    return (f"❌ Удалить кнопку {escape(item.label)}?\nВсе её неиспользованные ссылки будут отозваны.",
            [[b("🗑 Да, удалить", f"x:idel:{item_id}", "danger"), b("✖️ Отмена", f"a:item:{item_id}")]])


@action("idel", "links")
async def act_item_delete(ctx: Ctx, item_id: str):
    item = item_or_none(ctx, item_id)
    if item is not None:
        await ctx.app.links.revoke("item_id = ?", (item.id,))
        await ctx.app.db.execute("DELETE FROM items WHERE id = ?", (item.id,))
        await ctx.reload()
        await ctx.log("item.delete", item.label)
        ctx.notice = f"🗑 Кнопка {escape(item.label)} удалена"
    return "a:items"


# ---------- привязка чата ----------
@view("bind", "links")
async def view_bind(ctx: Ctx, item_id: str) -> ViewResult:
    item = item_or_none(ctx, item_id)
    if item is None:
        return await view_items(ctx)
    ctx.app.start_bind(ctx.user_id, "item", item.id)
    base = f"https://t.me/{ctx.app.bot_username}"
    html = (
        f"🔗 <b>Чат для кнопки {escape(item.label)}</b>\n\n"
        "Нажми ➕ Добавить в канал или ➕ Добавить в группу и выбери нужный чат - "
        "право Приглашать пользователей там уже отмечено. Бот сам привяжется к этой кнопке "
        "и пришлёт подтверждение.\n\n"
        "Жду 10 минут. Если бот уже админ в нужном чате - выбери его из подключённых."
    )
    if item.chat_id:
        html += f"\n\nСейчас: {chat_title(ctx, item.chat_id)}. После замены старые ссылки будут отозваны."
    rows: Rows = [
        [button("➕ Добавить в канал", url=f"{base}?startchannel&admin=invite_users")],
        [button("➕ Добавить в группу", url=f"{base}?startgroup=bind&admin=invite_users")],
        [b("📋 Выбрать из подключённых", f"a:pick:{item.id}")],
        [b("✖️ Отмена", f"x:bindno:{item.id}")],
    ]
    return html, rows


@action("bindno", "links")
async def act_bind_cancel(ctx: Ctx, item_id: str):
    ctx.app.pending_binds.pop(ctx.user_id, None)
    return f"a:item:{item_id}"


@view("pick", "links")
async def view_pick(ctx: Ctx, item_id: str) -> ViewResult:
    chats = [c for c in ctx.app.store.chats.values() if c.can_invite and c.is_present]
    if not chats:
        ctx.notice = "Бот пока нигде не админ с правом приглашать. Добавь его в чат кнопкой выше."
        return await view_bind(ctx, item_id)
    rows: Rows = [[b(f"{'📢' if c.type == 'channel' else '👥'} {c.title}", f"x:bindto:{item_id}:{c.id}")] for c in chats]
    rows.append(back_btn(f"a:bind:{item_id}"))
    return "📋 <b>Выбери чат для кнопки</b>", rows


@action("bindto", "links")
async def act_bind_to(ctx: Ctx, item_id: str, chat_id: str):
    chat = ctx.app.store.chats.get(int(chat_id))
    if chat is None or not chat.can_invite or item_or_none(ctx, item_id) is None:
        await ctx.toast("Этот чат больше недоступен.", alert=True)
        return f"a:item:{item_id}"
    ctx.app.pending_binds.pop(ctx.user_id, None)
    await ctx.app.links.bind(int(item_id), chat.id)
    await ctx.log("item.bind", f"{item_id}: {chat.title}")
    ctx.notice = f"✅ Кнопка ведёт в {escape(chat.title)}"
    return f"a:item:{item_id}"


# ---------- чаты ----------
@view("chats", "links")
async def view_chats(ctx: Ctx) -> ViewResult:
    store = ctx.app.store
    lines = []
    rows: Rows = []
    for c in sorted(store.chats.values(), key=lambda c: (not c.is_present, c.title.lower())):
        state = "✅ админ" if c.can_invite else ("⚠️ нет права приглашать" if c.is_present else "🚪 бота убрали")
        used = [i.label for i in store.items.values() if i.chat_id == c.id]
        used += [f"спонсор {s.title}" for s in store.sponsor_chats.get(c.id, []) if s.is_active]
        lines.append(f"{'📢' if c.type == 'channel' else '👥'} <b>{escape(c.title)}</b> - {state}"
                     + (f"\n   ↳ {escape(', '.join(used))}" if used else ""))
        if not c.is_present and not used:
            rows.append([b(f"🧹 Забыть {c.title[:30]}", f"x:chforget:{c.id}")])
    rows.append(back_btn("a:items"))
    html = "🔌 <b>Чаты бота</b>\n\n" + ("\n".join(lines) or "Бот пока ни в одном чате.")
    return html, rows


@action("chforget", "links")
async def act_chat_forget(ctx: Ctx, chat_id: str):
    await ctx.app.db.execute("DELETE FROM chats WHERE id = ? AND is_present = 0", (int(chat_id),))
    await ctx.reload()
    return "a:chats"
