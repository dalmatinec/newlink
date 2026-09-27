"""Фоновые задачи: сброс буферов, отзыв протухших ссылок, сроки рекламы, бэкап."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile

from .app import App
from .backup import make_backup
from .middlewares import GuardMiddleware
from .store import now

log = logging.getLogger(__name__)

TICK = 30
MAINTENANCE_EVERY = 60
LOG_KEEP_DAYS = 90
SEND_LIMIT = 49 * 1024 * 1024  # бот может отправить файл до 50 МБ


async def run_jobs(app: App, guard: GuardMiddleware) -> None:
    ticks = 0
    while True:
        await asyncio.sleep(TICK)
        ticks += TICK
        try:
            await app.flush()
            if ticks % MAINTENANCE_EVERY == 0:
                guard.cleanup()
                await app.links.cleanup()
                await app.ads.check_schedule()
                await expire_bans(app)
                await daily(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка фоновой задачи")


async def expire_bans(app: App) -> None:
    t = now()
    for uid in [u for u, until in app.banned.items() if until is not None and until <= t]:
        await app.unban(uid)


async def daily(app: App) -> None:
    s = app.store.setting
    local = datetime.now(timezone(timedelta(hours=int(s("tz_offset", 3)))))
    today = local.strftime("%Y-%m-%d")
    if local.hour < int(s("backup_hour", 4)) or s("last_backup_day", "") == today:
        return
    await app.set_setting("last_backup_day", today)
    await app.db.execute("DELETE FROM admin_log WHERE ts < ?", (now() - LOG_KEEP_DAYS * 86400,))
    await app.db.execute("PRAGMA optimize")
    await send_backup(app, "💾 Ежедневный бэкап")
    removed = await app.media.collect_garbage()
    if removed:
        log.info("Удалено неиспользуемых медиа: %s", removed)


async def send_backup(app: App, caption: str) -> str:
    """Бэкап на сервер (там лежат 3 последних) + копия в канал логов, а без канала - владельцам."""
    path = await make_backup(app)
    size = f"{path.stat().st_size / 1048576:.1f} МБ"
    on_server = f"На сервере: <code>{escape(str(path.parent.resolve()))}</code>"
    if path.stat().st_size > SEND_LIMIT:
        await app.log_event(f"{caption}: {escape(path.name)} ({size}), в Telegram не влезает.\n{on_server}")
        return f"✅ Бэкап готов, {size}. В Telegram не влезает, забирайте с сервера.\n{on_server}"
    data = await asyncio.to_thread(path.read_bytes)
    targets = [app.log_chat] if app.log_chat else sorted(app.config.owner_ids)
    errors = []
    for target in targets:
        try:
            await app.bot.send_document(target, BufferedInputFile(data, filename=path.name),
                                        caption=f"{caption} ({size})", disable_notification=True)
        except TelegramAPIError as e:
            errors.append(str(e))
    if errors:
        return f"⚠️ Бэкап готов ({size}), но в Telegram не ушёл: {escape(errors[0])[:200]}\n{on_server}"
    return f"✅ Бэкап готов, {size}, отправлен {'в канал логов' if app.log_chat else 'владельцу в личку'}.\n{on_server}"


