"""Ошибки из логов — в канал логов. Одинаковые ошибки не спамят: повтор не чаще раза в 10 минут."""
import asyncio
import logging
import time
import traceback
from html import escape

from .app import App

REPEAT_EVERY = 600


class TelegramLogHandler(logging.Handler):
    def __init__(self, app: App) -> None:
        super().__init__(level=logging.ERROR)
        self.app = app
        self.queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue(maxsize=200)
        self.loop = asyncio.get_running_loop()
        self.last_sent: dict[str, float] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("aiogram.event") and record.exc_info is None:
            return
        try:
            self.loop.call_soon_threadsafe(self._put, record)
        except RuntimeError:  # цикл уже закрыт — при остановке
            pass

    def _put(self, record: logging.LogRecord) -> None:
        if not self.queue.full():
            self.queue.put_nowait(record)

    async def run(self) -> None:
        while True:
            record = await self.queue.get()
            key = f"{record.name}:{record.getMessage()[:200]}"
            if record.exc_info and record.exc_info[1] is not None:
                key += type(record.exc_info[1]).__name__
            t = time.monotonic()
            if t - self.last_sent.get(key, -REPEAT_EVERY) < REPEAT_EVERY:
                continue
            self.last_sent[key] = t
            if len(self.last_sent) > 500:
                self.last_sent = {k: v for k, v in self.last_sent.items() if t - v < REPEAT_EVERY}
            await self.app.log_event(self.format_html(record))
            await asyncio.sleep(1)  # не упираться в лимиты Telegram

    @staticmethod
    def format_html(record: logging.LogRecord) -> str:
        text = f"❗️ <b>Ошибка</b> <code>{escape(record.name)}</code>\n{escape(record.getMessage()[:500])}"
        if record.exc_info:
            tb = "".join(traceback.format_exception(*record.exc_info))[-2500:]
            text += f"\n<pre>{escape(tb)}</pre>"
        return text
