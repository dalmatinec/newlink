import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

import database as db
from utils import is_admin, render


@dataclass
class _UserState:
    hits: deque = field(default_factory=deque)
    muted_until: float = 0.0
    last_text: str | None = None
    last_text_at: float = 0.0


class AntiFloodMiddleware(BaseMiddleware):
    """Антифлуд и антиспам для личных сообщений пользователей.

    Вешается только на роутер ЛС — в группе поддержки ограничений нет.
    Состояние в памяти: после перезапуска счётчики просто обнуляются.
    """

    def __init__(self) -> None:
        self._users: dict[int, _UserState] = {}

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if user is None or is_admin(user.id):
            return await handler(event, data)

        if db.is_banned(user.id):
            text = render("banned")
            if text:
                await event.answer(text)
            return None

        now = time.monotonic()
        state = self._users.setdefault(user.id, _UserState())

        # Уже в муте — молчим, предупреждение было один раз.
        if now < state.muted_until:
            return None

        limit = db.get_setting("flood_limit")
        window = db.get_setting("flood_window")
        while state.hits and now - state.hits[0] > window:
            state.hits.popleft()
        state.hits.append(now)

        if limit > 0 and len(state.hits) > limit:
            mute = db.get_setting("flood_mute")
            state.muted_until = now + mute
            state.hits.clear()
            await event.answer(render("flood", seconds=mute))
            return None

        if event.text is not None:
            max_len = db.get_setting("max_length")
            if max_len > 0 and len(event.text) > max_len:
                await event.answer(render("too_long", max=max_len))
                return None

            dup_window = db.get_setting("duplicate_window")
            if (
                dup_window > 0
                and not event.text.startswith("/")
                and event.text == state.last_text
                and now - state.last_text_at < dup_window
            ):
                await event.answer(render("duplicate"))
                return None
            state.last_text = event.text
            state.last_text_at = now

        return await handler(event, data)
