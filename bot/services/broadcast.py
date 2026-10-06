"""Рассылка в фоне: ~20 сообщений в секунду, прогресс виден в админке, можно остановить."""
import asyncio
import logging
from dataclasses import dataclass, field

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from ..app import App
from ..store import now

log = logging.getLogger(__name__)

AUDIENCES = {
    "all": ("👥 Всем", "is_blocked = 0 AND is_banned = 0"),
    "active7": ("🔥 Активным за 7 дней", "is_blocked = 0 AND is_banned = 0 AND last_seen > ?"),
    "active30": ("📅 Активным за 30 дней", "is_blocked = 0 AND is_banned = 0 AND last_seen > ?"),
}


@dataclass
class Broadcast:
    admin_id: int
    total: int
    started: int = field(default_factory=now)
    sent: int = 0
    blocked: int = 0
    failed: int = 0
    cancelled: bool = False
    finished: int | None = None

    @property
    def done(self) -> int:
        return self.sent + self.blocked + self.failed

    def summary(self) -> str:
        return (f"✅ Доставлено: <b>{self.sent}</b>\n🚫 Заблокировали бота: <b>{self.blocked}</b>\n"
                f"⚠️ Ошибки: <b>{self.failed}</b>\nВсего: {self.done} из {self.total}")


class Broadcaster:
    def __init__(self, app: App) -> None:
        self.app = app
        self.current: Broadcast | None = None
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def audience(self, key: str) -> list[int]:
        where = AUDIENCES.get(key, AUDIENCES["all"])[1]
        days = {"active7": 7, "active30": 30}.get(key)
        params = (now() - days * 86400,) if days else ()
        rows = await self.app.db.fetchall(f"SELECT id FROM users WHERE {where}", params)
        return [r["id"] for r in rows]

    def start(self, admin_id: int, from_chat: int, message_id: int, ids: list[int],
              kb: InlineKeyboardMarkup | None) -> None:
        """Рассылка копией сообщения админа."""
        async def send(uid: int) -> None:
            await self.app.bot.copy_message(uid, from_chat, message_id, reply_markup=kb)
        self._launch(admin_id, ids, send, "Рассылка")

    def start_text(self, admin_id: int, html: str, ids: list[int], title: str = "Рассылка") -> None:
        """Рассылка готового текста (например, уведомление об обновлении ссылок)."""
        async def send(uid: int) -> None:
            await self.app.bot.send_message(uid, html)
        self._launch(admin_id, ids, send, title)

    def _launch(self, admin_id: int, ids: list[int], send, title: str) -> None:
        self.current = Broadcast(admin_id, len(ids))
        self.task = asyncio.create_task(self._run(self.current, ids, send, title))

    async def _run(self, b: Broadcast, ids: list[int], send, title: str) -> None:
        bot = self.app.bot
        for uid in ids:
            if b.cancelled:
                break
            while True:
                try:
                    await send(uid)
                    b.sent += 1
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                except TelegramForbiddenError:
                    b.blocked += 1
                    await self.app.mark_blocked(uid)
                except TelegramAPIError:
                    b.failed += 1
                break
            await asyncio.sleep(0.05)
        b.finished = now()
        report = f"📣 <b>{title} {'остановлена' if b.cancelled else 'завершена'}</b>\n" + b.summary()
        try:
            await bot.send_message(b.admin_id, report)
        except TelegramAPIError:
            log.warning("Не удалось отправить отчёт о рассылке")
        await self.app.log_event(report)
