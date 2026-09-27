"""Админка: статистика, настройки, защита, канал логов, бэкап, журнал."""
import tempfile
from html import escape
from pathlib import Path

from aiogram.types import Message

from ...backup import restore_backup
from ...jobs import send_backup
from ...media import TELEGRAM_DOWNLOAD_LIMIT
from ...store import now
from .core import Ctx, InputError, Rows, ViewResult, action, b, back_btn, fmt_date, on_input, view, yes_no
from .journal import describe

# ключ -> (название, минимум, максимум)
SETTINGS = {
    "pool_size": ("⚡️ Запас ссылок на кнопку", 0, 50),
    "cleanup_hours": ("🧹 Отзывать неиспользованные через, ч", 0, 720),
    "sponsor_cache_minutes": ("⏱ Помнить проверку подписки, мин", 0, 1440),
    "tz_offset": ("🕒 Часовой пояс, UTC+", -12, 14),
    "backup_hour": ("💾 Час автобэкапа", 0, 23),
    "max_media_mb": ("🖼 Лимит картинок, МБ", 1, 20),
}
TOGGLES = {
    "strict_requests": "🔒 Заявки только от владельца",
    "clean_chat": "🧹 Удалять сообщения юзеров",
    "protect_content": "🚫 Запрет пересылки",
    "log_admin_actions": "📜 Действия админов в логи",
}
# разделы настроек: ключ -> (название, пояснение, настройки)
SETTING_GROUPS = {
    "links": ("⚡️ Ссылки",
              "<b>Запас</b>: бот заранее создаёт ссылки, и юзер получает свою мгновенно. 5 хватает, "
              "при больших наплывах можно 20-30.\n<b>Отзыв</b>: через сколько часов убирать ссылки, "
              "по которым не вошли (0 не убирать).\n<b>Заявки</b>: одобрять только того, кому выдана ссылка.",
              ["pool_size", "cleanup_hours", "strict_requests"]),
    "sponsors": ("🤝 Спонсоры",
                 "Сколько минут бот помнит, что человек подписан, и не проверяет его снова. "
                 "Меньше: точнее, больше: быстрее.", ["sponsor_cache_minutes"]),
    "chat": ("💬 Чат с юзером",
             "<b>Удалять сообщения</b>: в чате остаётся только экран бота.\n"
             "<b>Запрет пересылки</b>: юзер не сможет переслать или сохранить экраны бота.",
             ["clean_chat", "protect_content"]),
    "logs": ("📡 Логи и бэкап",
             "В канал логов приходят ошибки, бэкапы, отчёты рекламы и уведомления о сломанных чатах. "
             "Без канала бэкапы идут владельцу в личку.", ["log_chat", "log_admin_actions", "backup_hour"]),
    "other": ("🕒 Прочее", "Часовой пояс нужен для дат в отчётах и времени бэкапа.", ["tz_offset", "max_media_mb"]),
}
PROTECTION = {
    "flood_limit": ("Нажатий за окно (0 выключить)", 0, 100),
    "flood_window": ("Окно антифлуда, сек", 1, 60),
    "flood_strikes": ("Нарушений до автобана (0 без бана)", 0, 100),
    "flood_ban_minutes": ("Автобан, минут", 0, 100000),
}
ALL_NUMBERS = {**SETTINGS, **PROTECTION}


# ---------- статистика ----------
@view("stats", "stats")
async def view_stats(ctx: Ctx) -> ViewResult:
    app = ctx.app
    await app.flush()
    db, t, store = app.db, now(), app.store

    async def count(sql: str, *params) -> int:
        return await db.fetchval(sql, params) or 0

    periods = (t - 86400, t - 7 * 86400, t - 30 * 86400)
    seen = [await count("SELECT COUNT(*) FROM users WHERE last_seen > ?", p) for p in periods]
    new = [await count("SELECT COUNT(*) FROM users WHERE created_at > ?", p) for p in periods]
    issued = [await count("SELECT COUNT(*) FROM invite_links WHERE assigned_at > ?", p) for p in periods]
    joins = [await count("SELECT COUNT(*) FROM joins WHERE ts > ?", p) for p in periods]
    top = await db.fetchall("SELECT item_id, COUNT(*) AS n FROM joins WHERE ts > ? GROUP BY item_id "
                            "ORDER BY n DESC LIMIT 10", (periods[2],))
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    top_lines = "\n".join(
        f"{medals.get(i, f'{i}.')} {escape(store.items[r['item_id']].label) if r['item_id'] in store.items else '#' + str(r['item_id'])}"
        f" · <b>{r['n']}</b>" for i, r in enumerate(top, 1)) or "пока нет вступлений"
    ad_views = await count("SELECT COALESCE(SUM(views), 0) FROM ad_daily WHERE day = ?", app.ads.day())
    conv = f"{joins[2] * 100 // issued[2]}%" if issued[2] else "нет данных"
    html = (
        "📊 <b>Статистика</b> (сутки · неделя · месяц)\n\n"
        f"👥 Пользователей: <b>{await count('SELECT COUNT(*) FROM users')}</b>\n"
        f"Новых: <b>{new[0]}</b> · <b>{new[1]}</b> · <b>{new[2]}</b>\n"
        f"Заходили: <b>{seen[0]}</b> · <b>{seen[1]}</b> · <b>{seen[2]}</b>\n"
        f"Заблокировали бота: <b>{await count('SELECT COUNT(*) FROM users WHERE is_blocked = 1')}</b>\n\n"
        f"🔗 Выдано ссылок: <b>{issued[0]}</b> · <b>{issued[1]}</b> · <b>{issued[2]}</b>\n"
        f"✅ Вступили: <b>{joins[0]}</b> · <b>{joins[1]}</b> · <b>{joins[2]}</b>\n"
        f"Конверсия за месяц: <b>{conv}</b>\n"
        f"⚡️ Готовых ссылок в запасе: <b>{sum(app.links.pool_counts.values())}</b>\n\n"
        f"📢 Показов рекламы сегодня: <b>{ad_views}</b>\n"
        f"🤝 Подписок на спонсоров за сутки: "
        f"<b>{await count('SELECT COUNT(*) FROM sponsor_users WHERE ts > ?', periods[0])}</b>\n\n"
        f"🏆 <b>Кнопки за месяц</b> (вступления)\n{top_lines}"
    )
    return html, [[b("🔄 Обновить", "a:stats")], back_btn("a:home")]


# ---------- настройки ----------
def back_cb(back: str) -> str:
    """Куда вернуться после правки: g_<раздел> — раздел настроек, иначе экран по имени."""
    return f"a:setg:{back[2:]}" if back.startswith("g_") else f"a:{back}"


@view("set", "settings")
async def view_settings(ctx: Ctx) -> ViewResult:
    btns = [b(title, f"a:setg:{g}") for g, (title, _, _) in SETTING_GROUPS.items()]
    rows: Rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append(back_btn("a:cfg"))
    return "⚙️ <b>Настройки</b>\nВыбери раздел.", rows


@view("setg", "settings")
async def view_setting_group(ctx: Ctx, group: str) -> ViewResult:
    title, about, keys = SETTING_GROUPS.get(group, SETTING_GROUPS["links"])
    s = ctx.app.store.setting
    rows: Rows = []
    for key in keys:
        if key == "log_chat":
            rows.append([b(f"📡 Канал логов: {'подключён' if ctx.app.log_chat else 'не задан'}", "x:logchat")])
        elif key in TOGGLES:
            rows.append([b(f"{TOGGLES[key]}: {yes_no(s(key, 0))}", f"x:settog:{key}:g_{group}")])
        else:
            rows.append([b(f"{SETTINGS[key][0]}: {s(key)}", f"x:setv:{key}:g_{group}")])
    rows.append(back_btn("a:set"))
    return f"{title}\n\n{about}", rows


@view("prot", "settings")
async def view_protection(ctx: Ctx) -> ViewResult:
    s = ctx.app.store.setting
    rows: Rows = [[b(f"{title}: {s(key)}", f"x:setv:{key}:prot")] for key, (title, _, _) in PROTECTION.items()]
    rows.append(back_btn("a:cfg"))
    html = ("🛡 <b>Защита</b>\n\n"
            f"Больше <b>{s('flood_limit')}</b> нажатий за <b>{s('flood_window')}</b> сек - бот просит не спешить. "
            f"После <b>{s('flood_strikes')}</b> раз - бан на <b>{s('flood_ban_minutes')}</b> мин. На админов не действует.")
    return html, rows


@action("setv", "settings")
async def act_setting_value(ctx: Ctx, key: str, back: str):
    title, lo, hi = ALL_NUMBERS[key]
    return await ctx.ask("setv", f"<b>{title}</b>\nОтправь число от {lo} до {hi}.", back_cb(back), key, back)


@on_input("setv", "settings")
async def in_setting_value(ctx: Ctx, message: Message, key: str, back: str):
    title, lo, hi = ALL_NUMBERS[key]
    try:
        value = int((message.text or "").strip())
    except ValueError:
        raise InputError("Нужно целое число.")
    if not lo <= value <= hi:
        raise InputError(f"Число должно быть от {lo} до {hi}.")
    await ctx.app.set_setting(key, value)
    if key == "pool_size":
        ctx.app.links.wake.set()
    await ctx.log("settings", f"{key}={value}")
    ctx.notice = "✅ Сохранено"
    return back_cb(back)


@action("settog", "settings")
async def act_setting_toggle(ctx: Ctx, key: str, back: str):
    await ctx.app.set_setting(key, 0 if ctx.app.store.setting(key, 0) else 1)
    return back_cb(back)


@action("logchat", "settings")
async def act_log_chat(ctx: Ctx):
    return await ctx.ask(
        "logchat",
        "📡 <b>Канал логов</b>\n\n1. Создайте приватный канал.\n"
        "2. Добавьте бота администратором (с правом публиковать).\n"
        "3. Перешлите сюда любое сообщение из канала или отправьте его ID (начинается с -100).\n\n"
        "Отправьте <code>0</code>, чтобы отключить.", "a:setg:logs")


@on_input("logchat", "settings")
async def in_log_chat(ctx: Ctx, message: Message):
    chat_id = None
    origin = message.forward_origin
    if origin is not None and getattr(origin, "chat", None) is not None:
        chat_id = origin.chat.id
    elif (message.text or "").strip().lstrip("-").isdigit():
        chat_id = int(message.text.strip())
    if chat_id is None:
        raise InputError("Перешлите сообщение из канала или отправьте его ID.")
    if chat_id:
        try:
            await ctx.app.bot.send_message(chat_id, "✅ Канал подключён: сюда будут приходить логи, ошибки и бэкапы.")
        except Exception as e:
            raise InputError(f"Бот не может писать в этот канал ({e}). Добавьте бота администратором.")
    await ctx.app.set_setting("log_chat_id", chat_id)
    await ctx.log("logchat.set", str(chat_id))
    ctx.notice = "✅ Канал логов подключён." if chat_id else "Канал логов отключён."
    return "a:setg:logs"


# ---------- бэкап ----------
@view("bak", "backup")
async def view_backup(ctx: Ctx) -> ViewResult:
    s = ctx.app.store.setting
    html = ("💾 <b>Бэкап</b>: база и все картинки одним архивом\n\n"
            f"Каждый день в {s('backup_hour')}:00, последний: {s('last_backup_day', 'ещё не было')}\n"
            f"Копия уходит {'в канал логов' if ctx.app.log_chat else 'владельцу в личку'}.\n"
            f"На сервере: <code>{escape(str(ctx.app.config.backup_dir.resolve()))}</code> (3 последних).")
    rows = [[b("💾 Сделать бэкап сейчас", "x:bakgo", "success")],
            [b("♻️ Восстановить из архива", "x:bakrest", "danger")],
            back_btn("a:cfg")]
    return html, rows


@action("bakgo", "backup")
async def act_backup_now(ctx: Ctx):
    await ctx.toast("Собираю архив...")
    ctx.notice = await send_backup(ctx.app, "💾 Бэкап")
    await ctx.log("backup.create")
    return "a:bak"


@action("bakrest", "backup")
async def act_backup_restore(ctx: Ctx):
    return await ctx.ask(
        "bakrest",
        "⚠️ Текущие данные будут <b>заменены</b> данными из архива (копия текущей базы останется на сервере).\n\n"
        f"Пришлите zip-архив бэкапа файлом, до {TELEGRAM_DOWNLOAD_LIMIT // 1048576} МБ.\n"
        "Архив больше - восстановите на сервере: <code>python -m bot.restore архив.zip</code>", "a:bak")


@on_input("bakrest", "backup")
async def in_backup_restore(ctx: Ctx, message: Message):
    doc = message.document
    if doc is None or not (doc.file_name or "").endswith(".zip"):
        raise InputError("Нужен zip-файл бэкапа.")
    if (doc.file_size or 0) > TELEGRAM_DOWNLOAD_LIMIT:
        raise InputError("Архив больше 20 МБ. Восстановите его на сервере командой python -m bot.restore.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "restore.zip"
        await ctx.app.bot.download(doc.file_id, destination=path)
        try:
            await restore_backup(ctx.app, path)
        except (ValueError, OSError) as e:
            raise InputError(f"Архив не подошёл: {e}")
    await ctx.log("backup.restore", doc.file_name or "")
    ctx.notice = "✅ Данные восстановлены из архива."
    return "a:bak"


# ---------- журнал ----------
@view("log", "log")
async def view_log(ctx: Ctx, page: str = "0") -> ViewResult:
    p, size = max(0, int(page)), 20
    rows_db = await ctx.app.db.fetchall("SELECT * FROM admin_log ORDER BY id DESC LIMIT ? OFFSET ?", (size + 1, p * size))
    tz = int(ctx.app.store.setting("tz_offset", 3))
    names = {r["id"]: r["first_name"] for r in await ctx.app.db.fetchall(
        "SELECT id, first_name FROM users WHERE id IN (SELECT DISTINCT admin_id FROM admin_log)")}
    lines = [
        f"<code>{fmt_date(r['ts'], tz)}</code> "
        f"{'🤖 бот' if r['admin_id'] == 0 else escape(names.get(r['admin_id']) or str(r['admin_id']))}: "
        f"{describe(ctx.app, r['action'], r['details'])}"
        for r in rows_db[:size]
    ]
    nav = []
    if p > 0:
        nav.append(b("◀️ Новее", f"a:log:{p - 1}"))
    if len(rows_db) > size:
        nav.append(b("Старее ▶️", f"a:log:{p + 1}"))
    return "📜 <b>Журнал</b> (хранится 90 дней)\n\n" + ("\n".join(lines) or "пусто"), [nav, back_btn("a:cfg")]
