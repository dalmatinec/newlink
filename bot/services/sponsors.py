"""Спонсоры (обязательная подписка).

Бот — админ в канале спонсора и выдаёт свою ссылку: по ней точно видно, сколько людей пришло.
Режим «по заявке»: поданная заявка засчитывается как подписка (удобно для закрытых каналов).
Проверка подписки кэшируется, чтобы не спрашивать Telegram на каждое нажатие.
"""
import asyncio
import logging
from html import escape

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

from ..app import App
from ..store import Sponsor, now

log = logging.getLogger(__name__)

IN_CHAT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


class SponsorService:
    def __init__(self, app: App) -> None:
        self.app = app
        self.ok_cache: dict[tuple[int, int], int] = {}  # (user, sponsor) -> когда подтвердили подписку
        self._warned: set[int] = set()

    def active(self) -> list[Sponsor]:
        store = self.app.store
        if not store.setting("sponsors_enabled", 1):
            return []
        t = now()
        return [s for s in store.active_sponsors if not s.ends_at or s.ends_at > t]

    async def missing(self, user_id: int) -> list[Sponsor]:
        """Спонсоры, на которых юзер ещё не подписан."""
        sponsors = self.active()
        if not sponsors:
            return []
        t = now()
        ttl = int(self.app.store.setting("sponsor_cache_minutes", 30)) * 60
        todo = [s for s in sponsors if t - self.ok_cache.get((user_id, s.id), -ttl - 1) > ttl]
        if not todo:
            return []
        requested: set[int] = set()
        if any(s.link_mode == "request" for s in todo):
            requested = {r["sponsor_id"] for r in await self.app.db.fetchall(
                "SELECT sponsor_id FROM sponsor_users WHERE user_id = ? AND kind = 'request'", (user_id,))}
        check = [s for s in todo if s.id not in requested]
        results = await asyncio.gather(*(self._is_member(s, user_id) for s in check))
        ok = {s.id for s, r in zip(check, results) if r} | requested
        missing = []
        for s in todo:
            if s.id in ok:
                self.ok_cache[(user_id, s.id)] = t
            else:
                missing.append(s)
        if len(self.ok_cache) > 300_000:
            self.ok_cache.clear()
        return missing

    async def _is_member(self, sponsor: Sponsor, user_id: int) -> bool:
        try:
            m = await self.app.bot.get_chat_member(sponsor.chat_id, user_id)
        except TelegramAPIError as e:
            await self._broken(sponsor, str(e))
            return True  # спонсор сломан — юзеров не блокируем
        if m.status == ChatMemberStatus.RESTRICTED:
            return bool(getattr(m, "is_member", False))
        return m.status in IN_CHAT

    async def _broken(self, sponsor: Sponsor, reason: str) -> None:
        if sponsor.id in self._warned:
            return
        self._warned.add(sponsor.id)
        await self.app.alert(
            f"⚠️ Не могу проверить подписку на спонсора «{escape(sponsor.title)}»: {escape(reason)[:150]}.\n"
            "Пока проверка пропускается. Проверь, что бот — админ в этом канале.", perm="sponsors")

    # ---------- учёт ----------
    async def on_join(self, chat_id: int, link: str, user_id: int) -> bool:
        return await self._count(chat_id, link, user_id, "join")

    async def on_request(self, chat_id: int, link: str, user_id: int) -> bool:
        return await self._count(chat_id, link, user_id, "request")

    async def _count(self, chat_id: int, link: str, user_id: int, kind: str) -> bool:
        for sponsor in self.app.store.sponsor_chats.get(chat_id, []):
            if sponsor.url != link:
                continue
            added = await self.app.db.execute_rowcount(
                "INSERT OR IGNORE INTO sponsor_users(sponsor_id, user_id, kind, ts) VALUES (?, ?, ?, ?)",
                (sponsor.id, user_id, kind, now()))
            if added:
                col = "joins" if kind == "join" else "requests"
                await self.app.db.execute(f"UPDATE sponsors SET {col} = {col} + 1 WHERE id = ?", (sponsor.id,))
                setattr(sponsor, col, getattr(sponsor, col) + 1)
                self.ok_cache[(user_id, sponsor.id)] = now()
                if sponsor.is_active and sponsor.target and sponsor.progress >= sponsor.target:
                    await self.finish(sponsor.id, "набрана цель")
            return True
        return False

    async def create_link(self, chat_id: int, mode: str) -> str:
        invite = await self.app.bot.create_chat_invite_link(
            chat_id=chat_id, name="Спонсор · бот", creates_join_request=mode == "request")
        return invite.invite_link

    async def finish(self, sponsor_id: int, reason: str) -> None:
        await self.app.db.execute(
            "UPDATE sponsors SET is_active = 0, finished_at = ?, finish_reason = ? WHERE id = ?",
            (now(), reason, sponsor_id))
        await self.app.reload()
        s = self.app.store.sponsors.get(sponsor_id)
        if s:
            await self.app.alert(
                f"🏁 <b>Спонсор завершён</b>: «{escape(s.title)}» — {escape(reason)}.\n"
                f"Подписались: <b>{s.joins}</b>, заявок: <b>{s.requests}</b>.", perm="sponsors")

    async def check_schedule(self) -> None:
        t = now()
        for s in list(self.app.store.active_sponsors):
            if s.ends_at and s.ends_at <= t:
                await self.finish(s.id, "закончился срок")
