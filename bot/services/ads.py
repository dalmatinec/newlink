"""Реклама: подбор объявления для юзера, показ, учёт, автостоп.

Счётчики живут в памяти и пишутся в базу пачкой (вместе с остальными буферами), поэтому показ
рекламы не тормозит бота. Частота на человека проверяется одним запросом по первичному ключу.
"""
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from ..app import App
from ..store import PLACEMENTS, Ad, now
from ..ui import BLANK, button, markup

log = logging.getLogger(__name__)

FREQ_NAMES = {0: "каждый раз", -1: "один раз на человека"}


def freq_text(hours: int) -> str:
    return FREQ_NAMES.get(hours, f"не чаще раза в {hours} ч")


def ad_markup(ad: Ad) -> InlineKeyboardMarkup | None:
    rows = []
    for b in ad.buttons:
        label, url = b[0], b[1]
        icon = b[2] if len(b) > 2 else None
        style = b[3] if len(b) > 3 else None
        rows.append([button(label, icon, style, url=url)])
    return markup(rows)


class AdService:
    def __init__(self, app: App) -> None:
        self.app = app
        self.last_shown: dict[int, int] = {}                     # user_id -> ts, общая пауза между рекламами
        self._users: dict[tuple[int, int], tuple[int, int]] = {}  # (user, ad) -> (last_ts, +views) для записи
        self._seen: dict[tuple[int, int], tuple[int, int]] = {}   # (user, ad) -> (last_ts, всего views), свежее
        self._daily: dict[tuple[int, str], list[int]] = {}        # (ad, day) -> [views, uniques]
        self._dirty: set[int] = set()

    def day(self) -> str:
        tz = timezone(timedelta(hours=int(self.app.store.setting("tz_offset", 3))))
        return datetime.now(tz).strftime("%Y-%m-%d")

    # ---------- подбор ----------
    async def pick(self, user_id: int, placement: str) -> tuple[Ad, int] | None:
        """-> (объявление, сколько раз его уже видел этот человек)."""
        store = self.app.store
        if not store.setting("ads_enabled", 1):
            return None
        t = now()
        gap = int(store.setting("ad_gap_minutes", 10)) * 60
        if gap and t - self.last_shown.get(user_id, 0) < gap:
            return None
        cands = [a for a in store.active_ads if placement in a.placements
                 and (not a.ends_at or a.ends_at > t) and (not a.max_views or a.views < a.max_views)]
        if not cands:
            return None
        seen: dict[int, tuple[int, int]] = {}
        missing = [a.id for a in cands if (user_id, a.id) not in self._seen]
        if missing:
            rows = await self.app.db.fetchall(
                f"SELECT ad_id, last_ts, views FROM ad_users WHERE user_id = ? AND ad_id IN ({','.join('?' * len(missing))})",
                (user_id, *missing))
            seen = {r["ad_id"]: (r["last_ts"], r["views"]) for r in rows}
        eligible = []
        for a in cands:
            last, n = self._seen.get((user_id, a.id)) or seen.get(a.id, (0, 0))
            if a.freq_hours == -1 and n > 0:
                continue
            if a.freq_hours > 0 and last and t - last < a.freq_hours * 3600:
                continue
            eligible.append((n, a.views, a.id, a))
        if not eligible:
            return None
        n, _, _, ad = min(eligible)  # меньше всего видел этот человек, потом - меньше всего показов вообще
        return ad, n

    async def show(self, chat_id: int, user_id: int, placement: str) -> Message | None:
        picked = await self.pick(user_id, placement)
        if picked is None:
            return None
        ad, seen_before = picked
        msg = await self.send(ad, chat_id)
        if msg is None:
            return None
        self._record(ad, user_id, seen_before)
        if ad.max_views and ad.views >= ad.max_views:
            await self.finish(ad.id, "набран лимит показов")
        return msg

    async def send(self, ad: Ad, chat_id: int) -> Message | None:
        app = self.app
        kb = ad_markup(ad)
        try:
            msg = None
            if ad.media_id:
                msg = await app.media.send(app.bot, chat_id, ad.media_id, ad.html, kb, disable_notification=True)
            if msg is None:
                msg = await app.bot.send_message(chat_id, ad.html or BLANK, reply_markup=kb, disable_notification=True)
            return msg
        except TelegramForbiddenError:
            await app.mark_blocked(chat_id)
        except TelegramAPIError as e:
            log.warning("Реклама %s не отправилась в %s: %s", ad.id, chat_id, e)
        return None

    def _record(self, ad: Ad, user_id: int, seen_before: int) -> None:
        t = now()
        ad.views += 1
        first = seen_before == 0
        if first:
            ad.uniques += 1
        self._dirty.add(ad.id)
        self.last_shown[user_id] = t
        self._seen[(user_id, ad.id)] = (t, seen_before + 1)
        _, add = self._users.get((user_id, ad.id), (0, 0))
        self._users[(user_id, ad.id)] = (t, add + 1)
        d = self._daily.setdefault((ad.id, self.day()), [0, 0])
        d[0] += 1
        d[1] += int(first)

    async def flush(self) -> None:
        users, self._users = self._users, {}
        daily, self._daily = self._daily, {}
        dirty, self._dirty = self._dirty, set()
        db = self.app.db
        if users:
            await db.executemany(
                "INSERT INTO ad_users(user_id, ad_id, last_ts, views) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, ad_id) DO UPDATE SET last_ts = excluded.last_ts, views = views + excluded.views",
                ((uid, ad, ts, n) for (uid, ad), (ts, n) in users.items()))
        if daily:
            await db.executemany(
                "INSERT INTO ad_daily(ad_id, day, views, uniques) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ad_id, day) DO UPDATE SET views = views + excluded.views, uniques = uniques + excluded.uniques",
                ((ad, day, v[0], v[1]) for (ad, day), v in daily.items()))
        ads = self.app.store.ads
        if dirty:
            await db.executemany("UPDATE ads SET views = ?, uniques = ? WHERE id = ?",
                                 ((ads[i].views, ads[i].uniques, i) for i in dirty if i in ads))
        if len(self._seen) > 200_000:  # память под контролем: свежесть восстановится из базы
            self._seen.clear()
        if len(self.last_shown) > 200_000:
            self.last_shown.clear()

    # ---------- жизненный цикл ----------
    async def start(self, ad_id: int) -> None:
        await self.app.db.execute(
            "UPDATE ads SET status = 'active', started_at = COALESCE(started_at, ?), finished_at = NULL, "
            "finish_reason = NULL WHERE id = ?", (now(), ad_id))
        await self.app.reload()

    async def finish(self, ad_id: int, reason: str) -> None:
        await self.flush()
        await self.app.db.execute(
            "UPDATE ads SET status = 'finished', finished_at = ?, finish_reason = ? WHERE id = ? AND status != 'finished'",
            (now(), reason, ad_id))
        await self.app.reload()
        await self.app.alert(f"🏁 <b>Реклама завершена</b>: {escape(reason)}\n\n{await self.report(ad_id)}", perm="ads")

    async def check_schedule(self) -> None:
        t = now()
        for ad in list(self.app.store.active_ads):
            if ad.ends_at and ad.ends_at <= t:
                await self.finish(ad.id, "закончился срок")
            elif ad.max_views and ad.views >= ad.max_views:
                await self.finish(ad.id, "набран лимит показов")

    async def report(self, ad_id: int) -> str:
        """Отчёт для рекламодателя - можно переслать как есть."""
        await self.flush()
        db = self.app.db
        ad = await db.fetchone("SELECT * FROM ads WHERE id = ?", (ad_id,))
        if ad is None:
            return "Объявление не найдено."
        days = await db.fetchall("SELECT day, views, uniques FROM ad_daily WHERE ad_id = ? ORDER BY day DESC LIMIT 14",
                                 (ad_id,))
        tz = int(self.app.store.setting("tz_offset", 3))
        started = fmt_date(ad["started_at"], tz) if ad["started_at"] else "не запускалась"
        lines = [
            f"📊 <b>Отчёт: {escape(ad['title'])}</b>",
            f"Старт: {started}",
            f"Показов: <b>{ad['views']}</b>" + (f" из {ad['max_views']}" if ad["max_views"] else ""),
            f"Уникальных людей: <b>{ad['uniques']}</b>",
            f"Где: {', '.join(PLACEMENTS[p] for p in ad['placements'].split(',') if p in PLACEMENTS) or 'нигде'}",
        ]
        if ad["finished_at"]:
            lines.append(f"Завершена: {fmt_date(ad['finished_at'], tz)} ({escape(ad['finish_reason'] or '')})")
        if days:
            lines.append("\n<b>По дням</b> (показы / люди):")
            lines += [f"{d['day'][8:10]}.{d['day'][5:7]} - {d['views']} / {d['uniques']}" for d in reversed(days)]
        return "\n".join(lines)


def fmt_date(ts: int, tz: int) -> str:
    return datetime.fromtimestamp(ts, timezone(timedelta(hours=tz))).strftime("%d.%m.%Y %H:%M")
