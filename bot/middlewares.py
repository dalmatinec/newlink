"""До основной логики: учёт пользователя, бан, антифлуд. Работает только в личке, группы пропускает дальше."""
import asyncio
import time
from collections import deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .app import App
from .richtext import html_to_plain
from .store import now


class GuardMiddleware(BaseMiddleware):
    def __init__(self, app: App) -> None:
        self.app = app
        self.hits: dict[int, deque[float]] = {}
        self.strikes: dict[int, int] = {}
        self._tasks: set[asyncio.Task] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        chat = data.get("event_chat")
        if chat is not None and chat.type != "private":
            return await handler(event, data)  # события групп и каналов (миграция и т.п.)
        if user is None or user.is_bot:
            return None

        app = self.app
        await app.touch_user(user.id, user.username, user.first_name)
        perms = app.perms(user.id)
        data["perms"] = perms
        if perms is None:
            if app.is_banned(user.id):
                return await self._reject(event, app.store.text("banned").html, alert=True)
            if self._flooding(user.id):
                return await self._reject(event, app.store.text("flood").html)
        return await handler(event, data)

    def _flooding(self, user_id: int) -> bool:
        s = self.app.store.setting
        limit, window = int(s("flood_limit", 8)), float(s("flood_window", 3))
        if limit <= 0:
            return False
        t = time.monotonic()
        q = self.hits.setdefault(user_id, deque())
        while q and t - q[0] > window:
            q.popleft()
        if not q:
            self.strikes.pop(user_id, None)
        q.append(t)
        if len(q) <= limit:
            return False
        strikes = self.strikes[user_id] = self.strikes.get(user_id, 0) + 1
        max_strikes, minutes = int(s("flood_strikes", 5)), int(s("flood_ban_minutes", 30))
        if 0 < max_strikes <= strikes and minutes > 0:
            self.hits.pop(user_id, None)
            self.strikes.pop(user_id, None)
            task = asyncio.create_task(self._auto_ban(user_id, minutes))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return True

    async def _auto_ban(self, user_id: int, minutes: int) -> None:
        await self.app.ban(user_id, now() + minutes * 60, "антифлуд")
        await self.app.log_event(f"🚫 Автобан за флуд: <code>{user_id}</code> на {minutes} мин.")

    async def _reject(self, event: TelegramObject, html: str, alert: bool = False) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(html_to_plain(html)[:190] or "...", show_alert=alert)
            elif isinstance(event, Message):
                if (event.text or "").startswith("/start") and alert and html:
                    await event.answer(html)
                await event.delete()
        except TelegramAPIError:
            pass

    def cleanup(self) -> None:
        t = time.monotonic()
        for uid in [uid for uid, q in self.hits.items() if not q or t - q[-1] > 120]:
            self.hits.pop(uid, None)
            self.strikes.pop(uid, None)
