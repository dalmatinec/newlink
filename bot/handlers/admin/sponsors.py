"""Админка: спонсоры (обязательная подписка перед выдачей ссылки)."""
from html import escape

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from ...richtext import html_to_plain
from ...store import Sponsor, now
from ...ui import button
from .ads import parse_when
from .core import Ctx, InputError, Rows, ViewResult, action, b, back_btn, fmt_date, move, on_input, view

TARGETS = [0, 100, 300, 500, 1000, 3000, 5000, 10000]
LINK_MODES = {"plain": "🔗 Обычная ссылка", "request": "📨 Заявка = подписка"}


def _sp(ctx: Ctx, sponsor_id: str) -> Sponsor | None:
    return ctx.app.store.sponsors.get(int(sponsor_id)) if sponsor_id.isdigit() else None


def _progress(s: Sponsor) -> str:
    return f"{s.progress}/{s.target}" if s.target else str(s.progress)


@view("sps", "sponsors")
async def view_sponsors(ctx: Ctx) -> ViewResult:
    store = ctx.app.store
    sponsors = [s for s in store.sponsors.values()]
    on = store.setting("sponsors_enabled", 1)
    rows: Rows = [[b(f"{'▶️' if s.is_active else '⏸'} {s.title} · {_progress(s)}", f"a:sp:{s.id}")] for s in sponsors]
    rows.append([b("➕ Добавить спонсора", "a:spnew", "success")])
    rows.append([b("🟢 Проверка подписки включена" if on else "🔴 Проверка подписки выключена", "x:spsw")])
    rows.append(back_btn("a:home"))
    html = ("🤝 <b>Спонсоры</b> (обязательная подписка)\n\n"
            "Перед выдачей ссылки бот просит подписаться на активных спонсоров. Бот — админ в их каналах и выдаёт "
            "свою ссылку, поэтому точно считает, сколько людей пришло.\n\n"
            f"Активных: <b>{len(ctx.app.sponsors.active())}</b>")
    return html, rows


@action("spsw", "sponsors")
async def act_sponsors_switch(ctx: Ctx):
    await ctx.app.set_setting("sponsors_enabled", 0 if ctx.app.store.setting("sponsors_enabled", 1) else 1)
    return "a:sps"


@view("spnew", "sponsors")
async def view_sponsor_new(ctx: Ctx) -> ViewResult:
    ctx.app.start_bind(ctx.user_id, "sponsor")
    base = f"https://t.me/{ctx.app.bot_username}"
    html = ("➕ <b>Новый спонсор</b>\n\n"
            "Попроси владельца канала добавить бота админом (право «Приглашать пользователей»), "
            "или добавь сам кнопкой ниже, если канал твой. Бот сам создаст спонсора и пришлёт подтверждение.\n\n"
            "Жду 10 минут. Если бот уже админ в канале — выбери его из подключённых.")
    rows: Rows = [
        [button("➕ Добавить в канал", url=f"{base}?startchannel&admin=invite_users")],
        [button("➕ Добавить в группу", url=f"{base}?startgroup=sponsor&admin=invite_users")],
        [b("📋 Выбрать из подключённых", "a:sppick")],
        [b("✖️ Отмена", "x:spnewno")],
    ]
    return html, rows


@action("spnewno", "sponsors")
async def act_sponsor_new_cancel(ctx: Ctx):
    ctx.app.pending_binds.pop(ctx.user_id, None)
    return "a:sps"


@view("sppick", "sponsors")
async def view_sponsor_pick(ctx: Ctx) -> ViewResult:
    chats = [c for c in ctx.app.store.chats.values() if c.can_invite and c.is_present]
    if not chats:
        ctx.notice = "Бот пока нигде не админ с правом приглашать."
        return await view_sponsor_new(ctx)
    rows: Rows = [[b(f"{'📢' if c.type == 'channel' else '👥'} {c.title}", f"x:spto:{c.id}")] for c in chats]
    rows.append(back_btn("a:spnew"))
    return "📋 <b>Выбери канал спонсора</b>", rows


@action("spto", "sponsors")
async def act_sponsor_to(ctx: Ctx, chat_id: str):
    chat = ctx.app.store.chats.get(int(chat_id))
    if chat is None or not chat.can_invite:
        await ctx.toast("Этот чат недоступен.", alert=True)
        return "a:sps"
    ctx.app.pending_binds.pop(ctx.user_id, None)
    try:
        url = await ctx.app.sponsors.create_link(chat.id, "plain")
    except TelegramAPIError as e:
        await ctx.toast(f"Не получилось создать ссылку: {e}", alert=True)
        return "a:sps"
    pos = await ctx.app.db.fetchval("SELECT COALESCE(MAX(position), 0) + 1 FROM sponsors")
    sid = await ctx.app.db.execute("INSERT INTO sponsors(chat_id, title, url, position, created_at) VALUES (?, ?, ?, ?, ?)",
                                   (chat.id, chat.title, url, pos, now()))
    await ctx.reload()
    await ctx.log("sponsor.create", chat.title)
    ctx.notice = "✅ Спонсор добавлен и уже работает"
    return f"a:sp:{sid}"


@view("sp", "sponsors")
async def view_sponsor(ctx: Ctx, sponsor_id: str) -> ViewResult:
    s = _sp(ctx, sponsor_id)
    if s is None:
        return await view_sponsors(ctx)
    store = ctx.app.store
    tz = int(store.setting("tz_offset", 3))
    chat = store.chats.get(s.chat_id)
    row = await ctx.app.db.fetchone("SELECT finish_reason FROM sponsors WHERE id = ?", (s.id,))
    state = "▶️ активен" if s.is_active else f"⏸ остановлен{f' ({escape(row[0])})' if row and row[0] else ''}"
    html = (f"🤝 <b>{escape(s.title)}</b> — {state}\n\n"
            f"Канал: {escape(chat.title) if chat else s.chat_id}"
            f"{'' if chat is None or chat.can_invite else ' ⚠️ бот не админ'}\n"
            f"Ссылка: {escape(s.url)}\n"
            f"Режим: {LINK_MODES[s.link_mode]}\n"
            f"🎯 Цель: {s.target or 'без лимита'} · 📅 до: {fmt_date(s.ends_at, tz) if s.ends_at else 'без срока'}\n\n"
            f"✅ Подписались по ссылке бота: <b>{s.joins}</b>\n"
            f"📨 Подали заявку: <b>{s.requests}</b>")
    k = s.id
    rows: Rows = [
        [b("⏸ Остановить" if s.is_active else "▶️ Запустить", f"x:sprun:{k}"),
         b("✏️ Текст кнопки", f"x:sptitle:{k}")],
        [b(f"🔁 {LINK_MODES[s.link_mode]}", f"x:spmode:{k}")],
        [b(f"🎯 Цель: {s.target or '∞'}", f"a:sptgt:{k}"),
         b(f"📅 Срок: {fmt_date(s.ends_at, tz)[:5] if s.ends_at else '∞'}", f"x:spend:{k}")],
        [b("⬆️", f"x:spmv:{k}:-1"), b("⬇️", f"x:spmv:{k}:1"), b("🗑 Удалить", f"a:spdel:{k}", "danger")],
        back_btn("a:sps"),
    ]
    return html, rows


@action("sprun", "sponsors")
async def act_sponsor_run(ctx: Ctx, sponsor_id: str):
    s = _sp(ctx, sponsor_id)
    if s is None:
        return "a:sps"
    if not s.is_active and s.target and s.progress >= s.target:
        await ctx.toast("Цель уже набрана — увеличь цель.", alert=True)
        return f"a:sp:{sponsor_id}"
    await ctx.app.db.execute("UPDATE sponsors SET is_active = ?, finish_reason = NULL WHERE id = ?",
                             (int(not s.is_active), s.id))
    await ctx.reload()
    await ctx.log("sponsor.toggle", s.title)
    return f"a:sp:{sponsor_id}"


@action("sptitle", "sponsors")
async def act_sponsor_title(ctx: Ctx, sponsor_id: str):
    return await ctx.ask("sptitle", "✏️ Текст кнопки спонсора, который увидят пользователи:", f"a:sp:{sponsor_id}",
                         sponsor_id)


@on_input("sptitle", "sponsors")
async def in_sponsor_title(ctx: Ctx, message: Message, sponsor_id: str):
    title = html_to_plain(message.text or "").strip()[:60]
    if not title:
        raise InputError("Пустой текст.")
    await ctx.app.db.execute("UPDATE sponsors SET title = ? WHERE id = ?", (title, int(sponsor_id)))
    await ctx.reload()
    return f"a:sp:{sponsor_id}"


@action("spmode", "sponsors")
async def act_sponsor_mode(ctx: Ctx, sponsor_id: str):
    s = _sp(ctx, sponsor_id)
    if s is None:
        return "a:sps"
    new = "request" if s.link_mode == "plain" else "plain"
    try:
        url = await ctx.app.sponsors.create_link(s.chat_id, new)
    except TelegramAPIError as e:
        await ctx.toast(f"Не получилось создать ссылку: {e}", alert=True)
        return f"a:sp:{sponsor_id}"
    ctx.app.links.revoke_queue.put_nowait((s.chat_id, s.url))
    await ctx.app.db.execute("UPDATE sponsors SET link_mode = ?, url = ? WHERE id = ?", (new, url, s.id))
    await ctx.reload()
    ctx.notice = ("✅ Теперь поданная заявка считается подпиской. Одобрять заявки — на усмотрение владельца канала."
                  if new == "request" else "✅ Обычная ссылка: засчитывается только вступление.")
    return f"a:sp:{sponsor_id}"


@view("sptgt", "sponsors")
async def view_sponsor_target(ctx: Ctx, sponsor_id: str) -> ViewResult:
    s = _sp(ctx, sponsor_id)
    btns = [b(("✅ " if s and s.target == t else "") + (str(t) if t else "без лимита"), f"x:sptgt:{sponsor_id}:{t}")
            for t in TARGETS]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([b("✍️ Своё число", f"x:sptgtin:{sponsor_id}")])
    rows.append(back_btn(f"a:sp:{sponsor_id}"))
    return "🎯 <b>Цель по подпискам</b>\nКогда наберётся — спонсор отключится сам и придёт уведомление.", rows


@action("sptgt", "sponsors")
async def act_sponsor_target(ctx: Ctx, sponsor_id: str, target: str):
    await ctx.app.db.execute("UPDATE sponsors SET target = ? WHERE id = ?", (int(target), int(sponsor_id)))
    await ctx.reload()
    return f"a:sp:{sponsor_id}"


@action("sptgtin", "sponsors")
async def act_sponsor_target_input(ctx: Ctx, sponsor_id: str):
    return await ctx.ask("sptgt", "Сколько подписок нужно набрать? Пришли число (0 — без лимита).",
                         f"a:sptgt:{sponsor_id}", sponsor_id)


@on_input("sptgt", "sponsors")
async def in_sponsor_target(ctx: Ctx, message: Message, sponsor_id: str):
    text = (message.text or "").strip()
    if not text.isdigit():
        raise InputError("Нужно целое число.")
    return await act_sponsor_target(ctx, sponsor_id, text)


@action("spend", "sponsors")
async def act_sponsor_end(ctx: Ctx, sponsor_id: str):
    return await ctx.ask("spend", "📅 До какой даты держать спонсора? Пришли дату <code>31.12 18:00</code>, "
                                  "число дней от сегодня или <code>0</code> — без срока.", f"a:sp:{sponsor_id}",
                         sponsor_id)


@on_input("spend", "sponsors")
async def in_sponsor_end(ctx: Ctx, message: Message, sponsor_id: str):
    text = (message.text or "").strip()
    value = None
    if text != "0":
        value = parse_when(text, int(ctx.app.store.setting("tz_offset", 3)))
        if value is None or value <= now():
            raise InputError("Не понял дату или она уже прошла. Пример: 31.12 18:00")
    await ctx.app.db.execute("UPDATE sponsors SET ends_at = ? WHERE id = ?", (value, int(sponsor_id)))
    await ctx.reload()
    return f"a:sp:{sponsor_id}"


@action("spmv", "sponsors")
async def act_sponsor_move(ctx: Ctx, sponsor_id: str, delta: str):
    await move(ctx, "sponsors", int(sponsor_id), int(delta))
    return f"a:sp:{sponsor_id}"


@view("spdel", "sponsors")
async def view_sponsor_delete(ctx: Ctx, sponsor_id: str) -> ViewResult:
    return ("🗑 Удалить спонсора? Ссылка бота в его канале будет отозвана.",
            [[b("🗑 Удалить", f"x:spdel:{sponsor_id}", "danger"), b("✖️ Отмена", f"a:sp:{sponsor_id}")]])


@action("spdel", "sponsors")
async def act_sponsor_delete(ctx: Ctx, sponsor_id: str):
    s = _sp(ctx, sponsor_id)
    if s is not None:
        ctx.app.links.revoke_queue.put_nowait((s.chat_id, s.url))
        await ctx.app.db.execute("DELETE FROM sponsors WHERE id = ?", (s.id,))
        await ctx.app.db.execute("DELETE FROM sponsor_users WHERE sponsor_id = ?", (s.id,))
        await ctx.reload()
        await ctx.log("sponsor.delete", s.title)
    return "a:sps"
