"""Точка входа: python -m bot"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from .app import App
from .config import Config, load_config
from .db import Database
from .handlers.admin import router as admin_router
from .handlers.chats import router as chats_router
from .handlers.user import router as user_router
from .jobs import run_jobs
from .media import MediaStore
from .middlewares import GuardMiddleware
from .seed import apply_seed
from .services.ads import AdService
from .services.broadcast import Broadcaster
from .services.links import LinkService
from .services.updates import UpdateNotifier
from .store import Store
from .telelog import TelegramLogHandler

log = logging.getLogger("bot")


async def setup(config: Config, bot: Bot) -> tuple[App, Dispatcher, GuardMiddleware]:
    """Собирает приложение: база, кэш, сервисы, диспетчер. Отдельно от main - для тестов."""
    db = Database(config.db_path)
    await db.connect()
    await apply_seed(db)
    store = Store(db)
    await store.reload()

    me = await bot.get_me()
    media = MediaStore(db, config.media_dir)
    await media.load(me.id)
    app = App(config, db, store, media, bot, bot_username=me.username or "")
    app.links = LinkService(app)
    app.ads = AdService(app)
    app.broadcaster = Broadcaster(app)
    app.updates = UpdateNotifier(app)
    await app.load_users()

    async def on_missing(media_id: int) -> None:
        await app.notify_admins(f"⚠️ Картинка #{media_id} не найдена на диске, экран показан без неё. "
                                "Загрузите её заново.", perm="texts")
    media.on_missing = on_missing

    if store.setting("last_bot_id", 0) != me.id:
        log.info("Запущен бот @%s (id %s): медиа будут перезалиты", me.username, me.id)
        await app.set_setting("last_bot_id", me.id)

    dp = Dispatcher(storage=MemoryStorage(), app=app)
    guard = GuardMiddleware(app)
    dp.message.outer_middleware(guard)
    dp.callback_query.outer_middleware(guard)
    dp.include_router(chats_router)
    dp.include_router(admin_router)  # раньше пользовательского: ввод админа важнее чистки чата
    dp.include_router(user_router)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        if isinstance(event.exception, TelegramBadRequest) and "query is too old" in event.exception.message:
            return True
        log.exception("Необработанная ошибка", exc_info=event.exception)
        return True

    return app, dp, guard


async def main() -> None:
    config = load_config()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        import uvloop  # быстрее стандартного цикла, если установлен
        uvloop.install()
    except ImportError:
        pass
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    app, dp, guard = await setup(config, bot)
    media = app.media

    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])

    tasks = [
        asyncio.create_task(run_jobs(app, guard)),
        asyncio.create_task(app.links.refill_loop()),
        asyncio.create_task(app.links.revoke_worker()),
    ]
    app.links.wake.set()
    if media.pending_warmup():
        owner = min(config.owner_ids)

        async def warmup() -> None:
            ok, failed = await media.warmup(bot, owner)
            log.info("Прогрев медиа: загружено %s, ошибок %s", ok, failed)
        tasks.append(asyncio.create_task(warmup()))

    tg_log = TelegramLogHandler(app)
    logging.getLogger().addHandler(tg_log)
    tasks.append(asyncio.create_task(tg_log.run()))

    log.info("Бот @%s запущен", app.bot_username)
    await app.log_event(f"🟢 Бот @{app.bot_username} запущен. Кнопок: {len(app.store.menu)}, "
                        f"пользователей: {len(app.known_users)}.")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await app.log_event(f"🔴 Бот @{app.bot_username} остановлен.")
        logging.getLogger().removeHandler(tg_log)
        for t in tasks:
            t.cancel()
        await app.flush()
        await app.db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
