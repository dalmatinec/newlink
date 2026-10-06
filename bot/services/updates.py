"""Уведомление пользователей о смене ссылок.

Админ поменял чат у кнопки: бот ждёт минуту, собирая все замены, и одним сообщением сообщает всем,
кто запускал бота: "Наши ссылки на VIP, PRICE обновлены, нажмите /start". Админ получает
предупреждение с кнопками Отправить сейчас и Не отправлять.
"""
import asyncio
import logging
from html import escape

from aiogram.exceptions import TelegramAPIError

from ..app import App
from ..ui import button, fill, markup

log = logging.getLogger(__name__)

DELAY = 60  # секунд на то, чтобы успеть поменять ещё кнопки


class UpdateNotifier:
    def __init__(self, app: App) -> None:
        self.app = app
        self.delay = DELAY
        self.pending: dict[int, str] = {}  # id кнопки -> название
        self.task: asyncio.Task | None = None

    def changed(self, item_id: int, label: str) -> None:
        """У кнопки сменился чат. Первая замена запускает отсчёт и предупреждает админов."""
        if not self.app.store.setting("notify_link_change", 1):
            return
        first = not self.pending
        self.pending[item_id] = label
        if first:
            self.task = asyncio.create_task(self._later())
            asyncio.create_task(self._warn_admins())

    def preview(self) -> str:
        names = ", ".join(self.pending.values())
        return fill(self.app.store.text("links_updated").html, **{"кнопки": names})

    async def _warn_admins(self) -> None:
        kb = markup([[button("🚀 Отправить сейчас", cb="x:updnow", style="success"),
                      button("✖️ Не отправлять", cb="x:updno")]])
        await self.app.notify_admins(
            f"📣 Через минуту всем пользователям уйдёт сообщение:\n\n<blockquote>{self.preview()}</blockquote>\n\n"
            "Поменяешь за это время ещё кнопки, они попадут в это же сообщение.", perm="links", reply_markup=kb)

    async def _later(self) -> None:
        try:
            await asyncio.sleep(self.delay)
            await self.send_now()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Не удалось разослать уведомление об обновлении ссылок")

    async def send_now(self, admin_id: int | None = None) -> int:
        """Разослать сейчас. Возвращает число получателей (0, если нечего слать)."""
        if not self.pending:
            return 0
        html = self.preview()
        names = ", ".join(self.pending.values())
        self.pending = {}
        task, self.task = self.task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        br = self.app.broadcaster
        while br.running:  # не мешаем идущей рассылке
            await asyncio.sleep(5)
        ids = await br.audience("all")
        owner = admin_id or min(self.app.config.owner_ids)
        br.start_text(owner, html, ids, "Рассылка об обновлении ссылок")
        await self.app.log_action(admin_id or 0, "links.notify", f"{len(ids)}: {names}",
                                  f"📣 Уведомление об обновлении ссылок ({escape(names)}) ушло {len(ids)} пользователям")
        return len(ids)

    def cancel(self) -> bool:
        if not self.pending:
            return False
        self.pending = {}
        if self.task is not None:
            self.task.cancel()
            self.task = None
        return True


async def safe_edit(app: App, chat_id: int, message_id: int, html: str) -> None:
    try:
        await app.bot.edit_message_text(text=html, chat_id=chat_id, message_id=message_id)
    except TelegramAPIError:
        pass
