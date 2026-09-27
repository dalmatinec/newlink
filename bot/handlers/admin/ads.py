"""Админка: реклама. Создание в два шага: переслал пост → ▶️ Запустить. Остальное - по желанию."""
import json
import re
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram.types import Message

from ...richtext import html_to_plain, message_html, parse_contacts
from ...services.ads import freq_text
from ...store import PLACEMENTS, now
from .core import (
    Ctx, InputError, Rows, ViewResult, action, b, back_btn, fmt_date, has_media, on_input, premium_notice, preview,
    save_media, style_name, view,
)

PLACE_SHORT = {"start": "После /start", "link": "После ссылки"}
STATUS = {"active": "▶️ идёт", "paused": "⏸ на паузе", "draft": "📝 черновик", "finished": "🏁 завершена"}
LIMITS = [0, 1000, 3000, 5000, 10000, 25000, 50000, 100000]
DAYS = [0, 1, 3, 7, 14, 30]
FREQS = [0, 1, 3, 6, 12, 24, -1]
GAPS = [0, 5, 10, 30, 60, 180]


def limit_text(n: int) -> str:
    return "без лимита" if not n else f"{n:,}".replace(",", " ")


async def _ad(ctx: Ctx, ad_id: str):
    return await ctx.app.db.fetchone("SELECT * FROM ads WHERE id = ?", (int(ad_id),)) if ad_id.isdigit() else None


# ---------- список ----------
@view("ads", "ads")
async def view_ads(ctx: Ctx) -> ViewResult:
    await ctx.app.ads.flush()
    rows_db = await ctx.app.db.fetchall(
        "SELECT id, title, status, views FROM ads WHERE status != 'finished' OR finished_at > ? "
        "ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 WHEN 'draft' THEN 2 ELSE 3 END, id DESC "
        "LIMIT 40", (now() - 30 * 86400,))
    s = ctx.app.store.setting
    rows: Rows = [[b(f"{STATUS[r['status']].split()[0]} {r['title']} · 👁 {r['views']}", f"a:ad:{r['id']}")]
                  for r in rows_db]
    rows.append([b("➕ Новая реклама", "x:adnew", "success")])
    rows.append([b(f"⚙️ Общие настройки: {'вкл' if s('ads_enabled', 1) else '🔴 выкл'}", "a:adset")])
    rows.append(back_btn("a:home"))
    html = ("📢 <b>Реклама</b>\n\n"
            "Создать: ➕ Новая реклама → перешли или отправь пост → ▶️ Запустить. "
            "Кнопки из пересланного поста подтянутся сами.\n\n"
            f"Идёт сейчас: <b>{sum(r['status'] == 'active' for r in rows_db)}</b>")
    return html, rows


@view("adset", "ads")
async def view_ad_settings(ctx: Ctx) -> ViewResult:
    s = ctx.app.store.setting
    gap = int(s("ad_gap_minutes", 10))
    rows: Rows = [[b(f"{'🟢 Реклама включена' if s('ads_enabled', 1) else '🔴 Реклама выключена'}", "x:adsw")]]
    btns = [b(("✅ " if gap == g else "") + (f"{g} мин" if g else "без паузы"), f"x:adgap:{g}") for g in GAPS]
    rows += [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append(back_btn("a:ads"))
    return ("⚙️ <b>Общие настройки рекламы</b>\n\n"
            "Пауза между рекламами для одного человека: даже если объявлений много, "
            f"юзер увидит не больше одного раз в <b>{gap} мин</b>. Так люди не устают от рекламы.\n\n"
            "Выключатель останавливает показ всех объявлений разом (счётчики и настройки сохраняются)."), rows


@action("adsw", "ads")
async def act_ads_switch(ctx: Ctx):
    await ctx.app.set_setting("ads_enabled", 0 if ctx.app.store.setting("ads_enabled", 1) else 1)
    return "a:adset"


@action("adgap", "ads")
async def act_ads_gap(ctx: Ctx, minutes: str):
    await ctx.app.set_setting("ad_gap_minutes", int(minutes))
    return "a:adset"


# ---------- создание ----------
POST_HELP = ("📝 <b>Перешли сюда рекламный пост</b> (из канала или от рекламодателя) или отправь его сам: "
             "текст, фото, GIF или видео с подписью. Форматирование и премиум-эмодзи сохранятся.\n\n"
             "Кнопки-ссылки из пересланного поста подтянутся сами. Добавить или поменять их можно потом.")


@action("adnew", "ads")
async def act_ad_new(ctx: Ctx):
    return await ctx.ask("adpost", POST_HELP, "a:ads", "new")


@action("adpost", "ads")
async def act_ad_post(ctx: Ctx, ad_id: str):
    return await ctx.ask("adpost", POST_HELP, f"a:admore:{ad_id}", ad_id)


def _post_buttons(message: Message) -> list[list]:
    kb = message.reply_markup
    out = []
    for row in (kb.inline_keyboard if kb else []):
        for btn in row:
            if btn.url:
                out.append([btn.text, btn.url, btn.icon_custom_emoji_id, btn.style])
    return out


@on_input("adpost", "ads")
async def in_ad_post(ctx: Ctx, message: Message, ad_id: str):
    html, plain = message_html(message)
    media_id = await save_media(ctx, message) if has_media(message) else None
    if not plain and media_id is None:
        raise InputError("Пришли текст или картинку/видео с подписью.")
    if media_id and len(plain) > 1024:
        raise InputError("С картинкой текст не длиннее 1024 символов.")
    buttons = _post_buttons(message)
    db = ctx.app.db
    if ad_id == "new":
        title = (plain.strip().splitlines() or ["Реклама"])[0][:40] or "Реклама"
        new_id = await db.execute(
            "INSERT INTO ads(title, html, media_id, buttons, created_at) VALUES (?, ?, ?, ?, ?)",
            (title, html, media_id, json.dumps(buttons, ensure_ascii=False), now()))
        await ctx.reload()
        await ctx.log("ad.create", title)
        ctx.notice = ("✅ Реклама создана. Проверь её 👁 Предпросмотром, при желании настрой лимит и срок, "
                      "и жми ▶️ Запустить." + await premium_notice(ctx, message))
        return f"a:ad:{new_id}"
    fields = {"html": html, "media_id": media_id}
    if buttons:
        fields["buttons"] = json.dumps(buttons, ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    await db.execute(f"UPDATE ads SET {cols} WHERE id = ?", (*fields.values(), int(ad_id)))
    await ctx.reload()
    ctx.notice = "✅ Пост обновлён" + await premium_notice(ctx, message)
    return f"a:admore:{ad_id}"


# ---------- карточка ----------
@view("ad", "ads")
async def view_ad(ctx: Ctx, ad_id: str) -> ViewResult:
    await ctx.app.ads.flush()
    ad = await _ad(ctx, ad_id)
    if ad is None:
        return await view_ads(ctx)
    tz = int(ctx.app.store.setting("tz_offset", 3))
    places = set(filter(None, ad["placements"].split(",")))
    buttons = json.loads(ad["buttons"])
    progress = ""
    if ad["max_views"]:
        pct = min(100, ad["views"] * 100 // ad["max_views"])
        progress = f" ({pct}%)"
    html = (
        f"📢 <b>{escape(ad['title'])}</b> - {STATUS[ad['status']]}\n\n"
        f"👁 Показов: <b>{ad['views']}</b> из {limit_text(ad['max_views'])}{progress} · людей: <b>{ad['uniques']}</b>\n"
        f"📍 Где: {', '.join(PLACEMENTS[p] for p in PLACEMENTS if p in places) or '⚠️ нигде - отметь ниже'}\n"
        f"📅 До: {fmt_date(ad['ends_at'], tz) if ad['ends_at'] else 'без срока'}\n"
        f"🔁 Одному человеку: {freq_text(ad['freq_hours'])}\n"
        f"🔘 Кнопок: {len(buttons)}\n\n"
        f"<blockquote>{preview(ad['html'], 300)}</blockquote>"
    )
    if ad["status"] == "finished" and ad["finish_reason"]:
        html += f"\n🏁 {escape(ad['finish_reason'])}"
    k = ad["id"]
    if ad["status"] == "active":
        run = b("⏸ Пауза", f"x:adrun:{k}:pause")
    elif ad["status"] == "finished":
        run = b("🔄 Запустить снова", f"x:adrun:{k}:start", "success")
    else:
        run = b("▶️ Запустить", f"x:adrun:{k}:start", "success")
    rows: Rows = [
        [b("👁 Предпросмотр", f"x:adprev:{k}"), run],
        [b(("✅ " if p in places else "▫️ ") + name, f"x:adpl:{k}:{p}") for p, name in PLACE_SHORT.items()],
        [b(f"👁 Лимит: {limit_text(ad['max_views'])}", f"a:adlim:{k}"),
         b(f"📅 Срок: {fmt_date(ad['ends_at'], tz)[:5] if ad['ends_at'] else '∞'}", f"a:adend:{k}")],
        [b("📊 Отчёт", f"a:adrep:{k}"), b("⚙️ Ещё", f"a:admore:{k}")],
        back_btn("a:ads"),
    ]
    return html, rows


@view("admore", "ads")
async def view_ad_more(ctx: Ctx, ad_id: str) -> ViewResult:
    ad = await _ad(ctx, ad_id)
    if ad is None:
        return await view_ads(ctx)
    k = ad["id"]
    buttons = json.loads(ad["buttons"])
    color = buttons[0][3] if buttons and len(buttons[0]) > 3 else None
    rows: Rows = [
        [b(f"🔁 Частота: {freq_text(ad['freq_hours'])}", f"a:adfreq:{k}")],
        [b(f"🔘 Кнопки: {len(buttons)}", f"x:adbtn:{k}"), b(f"🎨 {style_name(color).capitalize()}", f"x:adcol:{k}:m", color)],
        [b("📝 Заменить пост", f"x:adpost:{k}"), b("✏️ Название", f"x:adttl:{k}")],
        [b("📑 Копия", f"x:adcopy:{k}"), b("🗑 Удалить", f"a:addel:{k}", "danger")],
        back_btn(f"a:ad:{k}"),
    ]
    html = (f"⚙️ <b>Ещё: {escape(ad['title'])}</b>\n\n"
            "🔁 <b>Частота</b>: как часто один человек видит эту рекламу.\n"
            "🎨 <b>Цвет</b>: цвет кнопок под рекламой.\n"
            "📑 <b>Копия</b>: новая реклама с тем же постом и нулевыми счётчиками.")
    return html, rows


@action("adrun", "ads")
async def act_ad_run(ctx: Ctx, ad_id: str, what: str):
    ad = await _ad(ctx, ad_id)
    if ad is None:
        return "a:ads"
    if what == "pause":
        await ctx.app.db.execute("UPDATE ads SET status = 'paused' WHERE id = ?", (ad["id"],))
        await ctx.reload()
        await ctx.log("ad.pause", ad["title"])
        return f"a:ad:{ad_id}"
    if not ad["placements"]:
        await ctx.toast("Сначала отметь, где показывать рекламу.", alert=True)
        return f"a:ad:{ad_id}"
    if ad["max_views"] and ad["views"] >= ad["max_views"]:
        await ctx.toast("Лимит показов уже набран - увеличь лимит или сделай копию.", alert=True)
        return f"a:ad:{ad_id}"
    if ad["ends_at"] and ad["ends_at"] <= now():
        await ctx.toast("Срок уже прошёл - поменяй срок.", alert=True)
        return f"a:ad:{ad_id}"
    await ctx.app.ads.start(ad["id"])
    await ctx.log("ad.start", ad["title"])
    if not ctx.app.store.setting("ads_enabled", 1):
        ctx.notice = "▶️ Запущена, но реклама выключена в общих настройках - включи её там."
    else:
        ctx.notice = "▶️ Реклама запущена"
    return f"a:ad:{ad_id}"


@action("adprev", "ads")
async def act_ad_preview(ctx: Ctx, ad_id: str):
    ad = ctx.app.store.ads.get(int(ad_id))
    if ad is None:
        return "a:ads"
    await ctx.app.ads.send(ad, ctx.chat_id)
    await ctx.toast("👇 Так её увидят пользователи")
    return None


@action("adpl", "ads")
async def act_ad_place(ctx: Ctx, ad_id: str, place: str):
    ad = await _ad(ctx, ad_id)
    if ad is None or place not in PLACEMENTS:
        return "a:ads"
    places = set(filter(None, ad["placements"].split(",")))
    places.symmetric_difference_update({place})
    await ctx.app.db.execute("UPDATE ads SET placements = ? WHERE id = ?", (",".join(sorted(places)), ad["id"]))
    await ctx.reload()
    return f"a:ad:{ad_id}"


def _presets(values: list[int], current: int, cb: str, label) -> Rows:
    btns = [b(("✅ " if current == v else "") + label(v), f"{cb}:{v}") for v in values]
    return [btns[i:i + 3] for i in range(0, len(btns), 3)]


@view("adlim", "ads")
async def view_ad_limit(ctx: Ctx, ad_id: str) -> ViewResult:
    ad = await _ad(ctx, ad_id)
    rows = _presets(LIMITS, ad["max_views"], f"x:adset1:{ad_id}:max_views", limit_text)
    rows.append([b("✍️ Своё число", f"x:adin:{ad_id}:max_views")])
    rows.append(back_btn(f"a:ad:{ad_id}"))
    return "👁 <b>Лимит показов</b>\nКогда наберётся - реклама остановится сама и пришлёт отчёт.", rows


@view("adfreq", "ads")
async def view_ad_freq(ctx: Ctx, ad_id: str) -> ViewResult:
    ad = await _ad(ctx, ad_id)
    rows = _presets(FREQS, ad["freq_hours"], f"x:adset1:{ad_id}:freq_hours", freq_text)
    rows.append(back_btn(f"a:admore:{ad_id}"))
    return ("🔁 <b>Как часто показывать одному человеку</b>\n"
            "Один раз - каждый увидит рекламу только однажды: максимум охвата разных людей."), rows


@view("adend", "ads")
async def view_ad_end(ctx: Ctx, ad_id: str) -> ViewResult:
    rows = _presets(DAYS, -99, f"x:adend:{ad_id}", lambda d: f"{d} дн" if d else "без срока")
    rows.append([b("✍️ Своя дата", f"x:adin:{ad_id}:ends_at")])
    rows.append(back_btn(f"a:ad:{ad_id}"))
    return "📅 <b>До какого момента показывать</b>\nСрок считается от текущего момента.", rows


FIELDS = {"max_views", "freq_hours", "ends_at"}


@action("adset1", "ads")
async def act_ad_set(ctx: Ctx, ad_id: str, field: str, value: str):
    if field not in FIELDS:
        return f"a:ad:{ad_id}"
    await ctx.app.db.execute(f"UPDATE ads SET {field} = ? WHERE id = ?", (int(value), int(ad_id)))
    await ctx.reload()
    return f"a:admore:{ad_id}" if field == "freq_hours" else f"a:ad:{ad_id}"


@action("adend", "ads")
async def act_ad_end(ctx: Ctx, ad_id: str, days: str):
    ends = now() + int(days) * 86400 if int(days) else None
    await ctx.app.db.execute("UPDATE ads SET ends_at = ? WHERE id = ?", (ends, int(ad_id)))
    await ctx.reload()
    return f"a:ad:{ad_id}"


@action("adin", "ads")
async def act_ad_input(ctx: Ctx, ad_id: str, field: str):
    prompt = {
        "max_views": "Сколько показов? Пришли число (0 - без лимита).",
        "ends_at": "До какой даты показывать? Пришли дату <code>31.12</code> или <code>31.12.2026 18:00</code>, "
                   "либо число дней от сегодня.",
    }[field]
    return await ctx.ask("adin", prompt, f"a:ad:{ad_id}", ad_id, field)


@on_input("adin", "ads")
async def in_ad_input(ctx: Ctx, message: Message, ad_id: str, field: str):
    text = (message.text or "").strip()
    if field == "max_views":
        if not text.isdigit():
            raise InputError("Нужно целое число.")
        value: int | None = int(text)
    else:
        value = parse_when(text, int(ctx.app.store.setting("tz_offset", 3)))
        if value is None or value <= now():
            raise InputError("Не понял дату или она уже прошла. Пример: 31.12 18:00")
    await ctx.app.db.execute(f"UPDATE ads SET {field} = ? WHERE id = ?", (value, int(ad_id)))
    await ctx.reload()
    ctx.notice = "✅ Сохранено"
    return f"a:ad:{ad_id}"


def parse_when(text: str, tz: int) -> int | None:
    if text.isdigit():
        return now() + int(text) * 86400
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?:\s+(\d{1,2}):(\d{2}))?", text)
    if not m:
        return None
    zone = timezone(timedelta(hours=tz))
    today = datetime.now(zone)
    year = int(m[3]) if m[3] else today.year
    if year < 100:
        year += 2000
    try:
        dt = datetime(year, int(m[2]), int(m[1]), int(m[4] or 23), int(m[5] or 59), tzinfo=zone)
    except ValueError:
        return None
    if not m[3] and dt < today:
        dt = dt.replace(year=year + 1)
    return int(dt.timestamp())


@action("adbtn", "ads")
async def act_ad_buttons(ctx: Ctx, ad_id: str):
    return await ctx.ask(
        "adbtn",
        "🔘 <b>Кнопки рекламы</b>\nКаждая кнопка - отдельной строкой:\n"
        "<code>Перейти в канал | https://t.me/channel</code>\n<code>Сайт | https://site.com</code>\n\n"
        "Премиум-эмодзи в строке станет иконкой кнопки. Отправь <code>-</code>, чтобы убрать все кнопки.",
        f"a:admore:{ad_id}", ad_id)


@on_input("adbtn", "ads")
async def in_ad_buttons(ctx: Ctx, message: Message, ad_id: str):
    if (message.text or "").strip() == "-":
        buttons: list[list] = []
    else:
        parsed, errors = parse_contacts(message)
        if errors:
            raise InputError("; ".join(errors[:3]))
        if not parsed:
            raise InputError("Нет ни одной кнопки.")
        buttons = [[label, url, icon, None] for label, url, icon in parsed]
    await ctx.app.db.execute("UPDATE ads SET buttons = ? WHERE id = ?",
                             (json.dumps(buttons, ensure_ascii=False), int(ad_id)))
    await ctx.reload()
    ctx.notice = f"✅ Кнопок: {len(buttons)}"
    return f"a:admore:{ad_id}"


@action("adcol", "ads")
async def act_ad_color(ctx: Ctx, ad_id: str, back: str = ""):
    """Цвет всех кнопок рекламы: обычная → синяя → зелёная → красная."""
    from ...store import STYLES
    ad = await _ad(ctx, ad_id)
    if ad is None:
        return "a:ads"
    buttons = json.loads(ad["buttons"])
    if not buttons:
        await ctx.toast("Сначала добавь кнопки.", alert=True)
        return f"a:admore:{ad_id}"
    cur = buttons[0][3] if len(buttons[0]) > 3 else None
    new = STYLES[(STYLES.index(cur) + 1) % len(STYLES)] if cur in STYLES else STYLES[1]
    for btn in buttons:
        btn[2:] = [btn[2] if len(btn) > 2 else None, new]
    await ctx.app.db.execute("UPDATE ads SET buttons = ? WHERE id = ?",
                             (json.dumps(buttons, ensure_ascii=False), int(ad_id)))
    await ctx.reload()
    return f"a:admore:{ad_id}"


@action("adttl", "ads")
async def act_ad_title(ctx: Ctx, ad_id: str):
    return await ctx.ask("adttl", "✏️ Название рекламы (видно только в админке и в отчёте):", f"a:admore:{ad_id}", ad_id)


@on_input("adttl", "ads")
async def in_ad_title(ctx: Ctx, message: Message, ad_id: str):
    title = html_to_plain(message.text or "").strip()[:60]
    if not title:
        raise InputError("Пустое название.")
    await ctx.app.db.execute("UPDATE ads SET title = ? WHERE id = ?", (title, int(ad_id)))
    await ctx.reload()
    return f"a:admore:{ad_id}"


@action("adcopy", "ads")
async def act_ad_copy(ctx: Ctx, ad_id: str):
    new_id = await ctx.app.db.execute(
        "INSERT INTO ads(title, html, media_id, buttons, placements, max_views, freq_hours, created_at) "
        "SELECT title || ' (копия)', html, media_id, buttons, placements, max_views, freq_hours, ? FROM ads WHERE id = ?",
        (now(), int(ad_id)))
    await ctx.reload()
    ctx.notice = "📑 Копия создана с нулевыми счётчиками - настрой и запускай."
    return f"a:ad:{new_id}"


@view("adrep", "ads")
async def view_ad_report(ctx: Ctx, ad_id: str) -> ViewResult:
    html = await ctx.app.ads.report(int(ad_id))
    return html, [[b("📤 Прислать отдельным сообщением", f"x:adrepsend:{ad_id}")], back_btn(f"a:ad:{ad_id}")]


@action("adrepsend", "ads")
async def act_ad_report_send(ctx: Ctx, ad_id: str):
    await ctx.app.bot.send_message(ctx.chat_id, await ctx.app.ads.report(int(ad_id)))
    await ctx.toast("👇 Отчёт ниже - его можно переслать рекламодателю")
    return None


@view("addel", "ads")
async def view_ad_delete(ctx: Ctx, ad_id: str) -> ViewResult:
    return ("🗑 Удалить рекламу вместе со статистикой?",
            [[b("🗑 Удалить", f"x:addel:{ad_id}", "danger"), b("✖️ Отмена", f"a:admore:{ad_id}")]])


@action("addel", "ads")
async def act_ad_delete(ctx: Ctx, ad_id: str):
    k = int(ad_id)
    await ctx.app.ads.flush()
    for sql in ("DELETE FROM ads WHERE id = ?", "DELETE FROM ad_users WHERE ad_id = ?",
                "DELETE FROM ad_daily WHERE ad_id = ?"):
        await ctx.app.db.execute(sql, (k,))
    await ctx.reload()
    await ctx.log("ad.delete", ad_id)
    ctx.notice = "🗑 Реклама удалена"
    return "a:ads"
