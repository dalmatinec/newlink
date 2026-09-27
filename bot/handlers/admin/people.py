"""Админка: пользователи и баны, админы и права, рассылка."""
from html import escape

from aiogram.types import Message

from ...app import App
from ...richtext import parse_contacts
from ...services.broadcast import AUDIENCES
from ...store import now
from ...ui import button, grid, markup, paginate
from .core import PERMS, Ctx, InputError, Rows, ViewResult, action, b, back_btn, fmt_date, on_input, view


# ---------- пользователи ----------
@view("users", "users")
async def view_users(ctx: Ctx) -> ViewResult:
    await ctx.app.flush()
    db, t = ctx.app.db, now()
    q = db.fetchval
    html = (f"👥 <b>Пользователи</b>\n\n"
            f"Всего: <b>{await q('SELECT COUNT(*) FROM users')}</b>\n"
            f"Активны за сутки: <b>{await q('SELECT COUNT(*) FROM users WHERE last_seen > ?', (t - 86400,))}</b>\n"
            f"Новых за сутки / 7 дней: <b>{await q('SELECT COUNT(*) FROM users WHERE created_at > ?', (t - 86400,))}</b>"
            f" / <b>{await q('SELECT COUNT(*) FROM users WHERE created_at > ?', (t - 7 * 86400,))}</b>\n"
            f"Забанены: <b>{await q('SELECT COUNT(*) FROM users WHERE is_banned = 1')}</b>\n"
            f"Заблокировали бота: <b>{await q('SELECT COUNT(*) FROM users WHERE is_blocked = 1')}</b>")
    rows = [[b("🔍 Найти пользователя", "x:ufind")], [b("🚫 Забаненные", "a:banned:0")], back_btn("a:home")]
    return html, rows


@action("ufind", "users")
async def act_user_find(ctx: Ctx):
    return await ctx.ask("ufind", "Отправьте ID пользователя, @username или перешлите его сообщение.", "a:users")


@on_input("ufind", "users")
async def in_user_find(ctx: Ctx, message: Message):
    uid = None
    origin = message.forward_origin
    if origin is not None:
        sender = getattr(origin, "sender_user", None)
        if sender is None:
            raise InputError("Пользователь скрыл пересылку в настройках - пришлите его ID.")
        uid = sender.id
    text = (message.text or "").strip()
    if uid is None and text.lstrip("-").isdigit():
        uid = int(text)
    elif uid is None and text:
        uid = await ctx.app.db.fetchval("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (text.lstrip("@"),))
    if uid is None:
        raise InputError("Пользователь не найден (он должен хотя бы раз запустить бота).")
    return f"a:user:{uid}"


@view("user", "users")
async def view_user(ctx: Ctx, user_id: str) -> ViewResult:
    app = ctx.app
    uid = int(user_id)
    row = await app.db.fetchone("SELECT * FROM users WHERE id = ?", (uid,))
    tz = int(app.store.setting("tz_offset", 3))
    if row is None:
        html = f"👤 <code>{uid}</code>\nВ базе нет - бот его ещё не видел."
    else:
        ban = "нет"
        if row["is_banned"]:
            ban = "навсегда" if row["banned_until"] is None else f"до {fmt_date(row['banned_until'], tz)}"
            if row["ban_reason"]:
                ban += f" ({escape(row['ban_reason'])})"
        joins = await app.db.fetchval("SELECT COUNT(*) FROM joins WHERE user_id = ?", (uid,))
        html = (f"👤 <b>{escape(row['first_name'] or 'без имени')}</b> "
                f"{'@' + escape(row['username']) if row['username'] else ''}\n"
                f"ID: <code>{uid}</code>\n"
                f"Первый вход: {fmt_date(row['created_at'], tz)}\n"
                f"Последняя активность: {fmt_date(row['last_seen'], tz)}\n"
                f"Вступил по ссылкам: {joins}\n"
                f"Бан: {ban}\n"
                f"Заблокировал бота: {'да' if row['is_blocked'] else 'нет'}")
    rows: Rows = []
    if uid in app.config.owner_ids:
        html += "\n\n👑 Это владелец, его нельзя забанить."
    elif app.is_banned(uid):
        rows.append([b("✅ Разбанить", f"x:unban:{uid}", "success")])
    else:
        rows.append([b("⏳ 1 час", f"x:ban:{uid}:60"), b("⏳ 1 день", f"x:ban:{uid}:1440"),
                     b("⏳ 7 дней", f"x:ban:{uid}:10080")])
        rows.append([b("🚫 Навсегда", f"x:ban:{uid}:0", "danger")])
    rows.append(back_btn("a:users"))
    return html, rows


@action("ban", "users")
async def act_ban(ctx: Ctx, user_id: str, minutes: str):
    uid = int(user_id)
    if uid in ctx.app.config.owner_ids or ctx.app.perms(uid) is not None:
        ctx.notice = "⛔️ Админа нельзя забанить. Сначала снимите с него права."
        return f"a:user:{uid}"
    until = None if minutes == "0" else now() + int(minutes) * 60
    await ctx.app.ban(uid, until, f"админ {ctx.user_id}")
    await ctx.log("user.ban", f"{uid} на {minutes if minutes != '0' else '∞'} мин")
    return f"a:user:{uid}"


@action("unban", "users")
async def act_unban(ctx: Ctx, user_id: str):
    await ctx.app.unban(int(user_id))
    await ctx.log("user.unban", user_id)
    return f"a:user:{user_id}"


@view("banned", "users")
async def view_banned(ctx: Ctx, page: str = "0") -> ViewResult:
    rows_db = await ctx.app.db.fetchall("SELECT id, first_name FROM users WHERE is_banned = 1 ORDER BY id")
    chunk, p, pages = paginate(rows_db, int(page), 30)
    rows = grid([b(f"{r['first_name'] or r['id']}", f"a:user:{r['id']}") for r in chunk], 2)
    nav = []
    if p > 0:
        nav.append(b("◀️", f"a:banned:{p - 1}"))
    if pages > 1:
        nav.append(button(f"{p + 1}/{pages}", cb="noop"))
    if p < pages - 1:
        nav.append(b("▶️", f"a:banned:{p + 1}"))
    rows.append(nav)
    rows.append(back_btn("a:users"))
    return f"🚫 <b>Забаненные</b>: {len(rows_db)}", rows


# ---------- админы ----------
def _perm_names(perms: set[str]) -> str:
    if "*" in perms:
        return "все права"
    return ", ".join(PERMS[p].split(" ", 1)[1] for p in PERMS if p in perms) or "без прав"


async def _admin_list(app: App) -> list[tuple[int, str, set[str]]]:
    ids = [uid for uid in app.store.admins if uid not in app.config.owner_ids]
    users = {r["id"]: r for r in await app.db.fetchall(
        f"SELECT id, first_name, username FROM users WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    out = []
    for uid in ids:
        u = users.get(uid)
        name = (u["first_name"] or "") if u else ""
        if u and u["username"]:
            name = f"{name} @{u['username']}".strip()
        out.append((uid, name or "ещё не заходил в бота", app.store.admins[uid]))
    return out


@view("admins", "admins")
async def view_admins(ctx: Ctx) -> ViewResult:
    admins = await _admin_list(ctx.app)
    lines = [f"{i}. {escape(name)}\n<code>{uid}</code> · {escape(_perm_names(perms))}"
             for i, (uid, name, perms) in enumerate(admins, 1)]
    html = f"👮 <b>Админы</b>: {len(admins)}\n\n" + ("\n\n".join(lines) or "Пока никого. Нажмите ➕ Добавить.")
    rows: Rows = [[b("➕ Добавить", "x:adnew2", "success")]]
    rows += [[b(f"✏️ {name if name != 'ещё не заходил в бота' else uid}", f"a:adm:{uid}")] for uid, name, _ in admins]
    rows.append(back_btn("a:cfg"))
    return html, rows


@action("adnew2", "admins")
async def act_admin_new(ctx: Ctx):
    return await ctx.ask("adnew", "Отправьте ID будущего админа или перешлите его сообщение.", "a:admins")


@on_input("adnew", "admins")
async def in_admin_new(ctx: Ctx, message: Message):
    uid = None
    if message.forward_origin is not None:
        sender = getattr(message.forward_origin, "sender_user", None)
        uid = sender.id if sender else None
    elif (message.text or "").strip().isdigit():
        uid = int(message.text.strip())
    if uid is None:
        raise InputError("Нужен числовой ID (пересылка скрыта настройками приватности).")
    await ctx.app.db.execute(
        "INSERT OR IGNORE INTO admins(user_id, perms, added_by, created_at) VALUES (?, 'links', ?, ?)",
        (uid, ctx.user_id, now()))
    await ctx.reload()
    await ctx.log("admin.add", str(uid))
    ctx.notice = "✅ Админ добавлен с доступом к кнопкам. Отметьте, какие ещё разделы ему открыть."
    return f"a:adm:{uid}"


@view("adm", "admins")
async def view_admin(ctx: Ctx, user_id: str) -> ViewResult:
    perms = ctx.app.store.admins.get(int(user_id))
    if perms is None:
        return await view_admins(ctx)
    name = next((n for uid, n, _ in await _admin_list(ctx.app) if uid == int(user_id)), "")
    rows = grid([b(f"{'✅' if key in perms else '▫️'} {title}", f"x:adp:{user_id}:{key}") for key, title in PERMS.items()], 2)
    rows.append([b("🗑 Удалить из админов", f"x:addel2:{user_id}", "danger")])
    rows.append(back_btn("a:admins"))
    return f"👮 <b>{escape(name)}</b> <code>{user_id}</code>\nОтметьте, какие разделы ему открыть.", rows


@action("adp", "admins")
async def act_admin_perm(ctx: Ctx, user_id: str, perm: str):
    perms = set(ctx.app.store.admins.get(int(user_id), set()))
    perms.symmetric_difference_update({perm})
    await ctx.app.db.execute("UPDATE admins SET perms = ? WHERE user_id = ?", (",".join(sorted(perms)), int(user_id)))
    await ctx.reload()
    await ctx.log("admin.perms", f"{user_id}: {','.join(sorted(perms))}")
    return f"a:adm:{user_id}"


@action("addel2", "admins")
async def act_admin_delete(ctx: Ctx, user_id: str):
    await ctx.app.db.execute("DELETE FROM admins WHERE user_id = ?", (int(user_id),))
    await ctx.reload()
    await ctx.log("admin.remove", user_id)
    return "a:admins"


# ---------- рассылка ----------
drafts: dict[int, dict] = {}  # admin_id -> {"msg": id, "buttons": [...], "aud": key}


@view("bc", "broadcast")
async def view_broadcast(ctx: Ctx) -> ViewResult:
    br = ctx.app.broadcaster
    cur = br.current
    if br.running and cur is not None:
        pct = cur.done * 100 // max(1, cur.total)
        html = f"📣 <b>Идёт рассылка</b>: {pct}%\n\n{cur.summary()}"
        return html, [[b("🔄 Обновить", "a:bc"), b("⛔️ Остановить", "x:bcstop", "danger")], back_btn("a:home")]
    count = len(await br.audience("all"))
    html = (f"📣 <b>Рассылка</b>\n\nПолучателей: <b>{count}</b>\n"
            "Отправь любое сообщение: текст с премиум-эмодзи, фото, GIF, видео. Кнопки можно добавить на "
            "следующем шаге, перед отправкой будет предпросмотр.")
    if cur is not None and cur.finished:
        html += f"\n\n<b>Прошлая рассылка:</b>\n{cur.summary()}"
    return html, [[b("✍️ Создать рассылку", "x:bcnew", "success")], back_btn("a:home")]


@action("bcnew", "broadcast")
async def act_broadcast_new(ctx: Ctx):
    return await ctx.ask("bcnew", "Отправь сообщение для рассылки - люди получат его ровно в таком виде.", "a:bc",
                         keep_message=True)


@on_input("bcnew", "broadcast")
async def in_broadcast(ctx: Ctx, message: Message):
    drafts[ctx.user_id] = {"msg": message.message_id, "buttons": [], "aud": "all"}
    return "a:bcc"


def _draft_kb(draft: dict):
    return markup([[button(label, icon, url=url)] for label, url, icon in draft["buttons"]])


@view("bcc", "broadcast")
async def view_broadcast_confirm(ctx: Ctx) -> ViewResult:
    draft = drafts.get(ctx.user_id)
    if draft is None:
        return await view_broadcast(ctx)
    count = len(await ctx.app.broadcaster.audience(draft["aud"]))
    html = (f"☝️ Сообщение выше получат <b>{count}</b> пользователей.\n"
            f"Кнопок: <b>{len(draft['buttons'])}</b>\n\nПроверь 👁 Предпросмотром и жми ✅ Отправить.")
    rows: Rows = [
        [b(("• " if k == draft["aud"] else "") + name, f"x:bcaud:{k}") for k, (name, _) in AUDIENCES.items()],
        [b("🔘 Кнопки", "x:bcbtn"), b("👁 Предпросмотр", "x:bcprev")],
        [b("✅ Отправить", "x:bcgo", "success"), b("✖️ Отмена", "x:bcno")],
    ]
    return html, rows


@action("bcaud", "broadcast")
async def act_broadcast_audience(ctx: Ctx, key: str):
    if ctx.user_id in drafts and key in AUDIENCES:
        drafts[ctx.user_id]["aud"] = key
    return "a:bcc"


@action("bcbtn", "broadcast")
async def act_broadcast_buttons(ctx: Ctx):
    return await ctx.ask("bcbtn", "🔘 Кнопки - каждая отдельной строкой:\n<code>Текст | https://ссылка</code>\n"
                                  "Отправь <code>-</code>, чтобы убрать кнопки.", "a:bcc")


@on_input("bcbtn", "broadcast")
async def in_broadcast_buttons(ctx: Ctx, message: Message):
    draft = drafts.get(ctx.user_id)
    if draft is None:
        return "a:bc"
    if (message.text or "").strip() == "-":
        draft["buttons"] = []
        return "a:bcc"
    parsed, errors = parse_contacts(message)
    if errors or not parsed:
        raise InputError("; ".join(errors[:3]) or "Нет ни одной кнопки.")
    draft["buttons"] = parsed
    return "a:bcc"


@action("bcprev", "broadcast")
async def act_broadcast_preview(ctx: Ctx):
    draft = drafts.get(ctx.user_id)
    if draft is None:
        return "a:bc"
    await ctx.app.bot.copy_message(ctx.chat_id, ctx.chat_id, draft["msg"], reply_markup=_draft_kb(draft))
    await ctx.toast("👇 Так увидят пользователи")
    return None


@action("bcno", "broadcast")
async def act_broadcast_cancel(ctx: Ctx):
    drafts.pop(ctx.user_id, None)
    return "a:bc"


@action("bcgo", "broadcast")
async def act_broadcast_go(ctx: Ctx):
    br = ctx.app.broadcaster
    draft = drafts.pop(ctx.user_id, None)
    if draft is None or br.running:
        return "a:bc"
    ids = await br.audience(draft["aud"])
    br.start(ctx.user_id, ctx.chat_id, draft["msg"], ids, _draft_kb(draft))
    await ctx.log("broadcast.start", f"{len(ids)} получателей")
    ctx.notice = "🚀 Рассылка запущена. Пришлю отчёт, когда закончится."
    return "a:bc"


@action("bcstop", "broadcast")
async def act_broadcast_stop(ctx: Ctx):
    if ctx.app.broadcaster.current is not None:
        ctx.app.broadcaster.current.cancelled = True
    ctx.notice = "⛔️ Останавливаю..."
    return "a:bc"
