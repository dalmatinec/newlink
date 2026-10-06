"""Инвайт-ссылки.

Скорость: бот заранее держит по несколько готовых ссылок на каждую кнопку (пул). Нажатие кнопки - это один UPDATE в базе, без ожидания Telegram. Пул пополняется в фоне. Пустой пул - ссылка
создаётся на лету, как запасной вариант.

Порядок: одна живая ссылка на человека и кнопку (повторное нажатие отдаёт ту же), после входа ссылка
отзывается, протухшие и давно не использованные - тоже. Отзыв идёт фоновой очередью с паузами.
"""
import asyncio
import logging
from dataclasses import dataclass
from html import escape

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ChatMemberAdministrator

from ..app import App
from ..store import Item, now

log = logging.getLogger(__name__)

DEAD_KEEP_DAYS = 30
CREATE_PAUSE = 0.3  # пауза между созданием ссылок в запас: бережём лимиты Telegram


class LinkUnavailable(Exception):
    """Чат недоступен: бота выкинули, отобрали права или чат удалён."""


class LinkBusy(Exception):
    """Флуд-контроль Telegram или сеть - стоит повторить чуть позже."""


@dataclass(slots=True)
class Issued:
    link: str
    expires_at: int | None


class LinkService:
    def __init__(self, app: App) -> None:
        self.app = app
        self.revoke_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self.pool_counts: dict[int, int] = {}
        self.wake = asyncio.Event()
        self.create_pause = CREATE_PAUSE

    # ---------- выдача ----------
    async def issue(self, item: Item, user_id: int) -> Issued:
        db, t = self.app.db, now()
        row = await db.fetchone(
            "SELECT link, expires_at FROM invite_links WHERE user_id = ? AND item_id = ? AND revoked = 0 "
            "AND used_at IS NULL AND chat_id = ? AND mode = ? AND (expires_at IS NULL OR expires_at > ?) LIMIT 1",
            (user_id, item.id, item.chat_id, item.mode, t + 60),
        )
        if row is not None:
            return Issued(row["link"], row["expires_at"])

        expires = t + item.ttl_minutes * 60 if item.ttl_minutes else None
        link = await db.returning(
            "UPDATE invite_links SET user_id = ?, assigned_at = ?, expires_at = ? WHERE link = ("
            " SELECT link FROM invite_links WHERE item_id = ? AND chat_id = ? AND mode = ?"
            " AND user_id IS NULL AND revoked = 0 ORDER BY created_at LIMIT 1) RETURNING link",
            (user_id, t, expires, item.id, item.chat_id, item.mode),
        )
        self.wake.set()  # пул стал меньше - пополнить
        if link:
            self.pool_counts[item.id] = max(0, self.pool_counts.get(item.id, 1) - 1)
            return Issued(link, expires)

        link = await self._create(item, expire_date=expires)
        await db.execute(
            "INSERT INTO invite_links(link, item_id, chat_id, mode, user_id, created_at, assigned_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link, item.id, item.chat_id, item.mode, user_id, t, t, expires),
        )
        return Issued(link, expires)

    async def _create(self, item: Item, expire_date: int | None = None) -> str:
        kwargs: dict = {"chat_id": item.chat_id, "name": item.label[:32] or None, "expire_date": expire_date}
        if item.mode == "request":
            kwargs["creates_join_request"] = True
        else:
            kwargs["member_limit"] = 1
        try:
            invite = await self.app.bot.create_chat_invite_link(**kwargs)
        except TelegramRetryAfter as e:
            raise LinkBusy(f"флуд-контроль, {e.retry_after} с") from e
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            raise LinkUnavailable(e.message) from e
        except TelegramAPIError as e:
            raise LinkBusy(str(e)) from e
        return invite.invite_link

    # ---------- пул ----------
    async def refill_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            try:
                await self.refill()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка пополнения пула ссылок")
                await asyncio.sleep(10)

    async def refill(self) -> None:
        store, db = self.app.store, self.app.db
        target = max(0, int(store.setting("pool_size", 5)))
        ready = {i.id: i for i in store.menu if i.kind == "invite" and store.item_ready(i)}
        counts: dict[int, int] = {}
        stale: list[tuple[int, str]] = []
        for r in await db.fetchall(
                "SELECT link, item_id, chat_id, mode FROM invite_links WHERE user_id IS NULL AND revoked = 0"):
            item = ready.get(r["item_id"])
            if item is None or item.chat_id != r["chat_id"] or item.mode != r["mode"]:
                stale.append((r["chat_id"], r["link"]))  # кнопку выключили, сменили чат или режим
            else:
                counts[item.id] = counts.get(item.id, 0) + 1
        if stale:
            await db.executemany("UPDATE invite_links SET revoked = 1 WHERE link = ?", ((link,) for _, link in stale))
            for pair in stale:
                self.revoke_queue.put_nowait(pair)
        self.pool_counts = {item_id: counts.get(item_id, 0) for item_id in ready}

        for item in ready.values():
            while self.pool_counts[item.id] < target:
                try:
                    link = await self._create(item)
                except LinkBusy:
                    await asyncio.sleep(5)
                    return  # продолжим на следующем круге
                except LinkUnavailable as e:
                    await self.chat_failed(item.chat_id or 0, str(e))
                    break
                await db.execute(
                    "INSERT INTO invite_links(link, item_id, chat_id, mode, created_at) VALUES (?, ?, ?, ?, ?)",
                    (link, item.id, item.chat_id, item.mode, now()),
                )
                self.pool_counts[item.id] += 1
                await asyncio.sleep(self.create_pause)

    # ---------- отзыв ----------
    async def revoke(self, where: str, params: tuple = ()) -> int:
        """Отзывает живые ссылки по условию: сразу в базе, в Telegram - фоновой очередью."""
        rows = await self.app.db.fetchall(
            f"SELECT link, chat_id FROM invite_links WHERE revoked = 0 AND used_at IS NULL AND ({where})", params)
        if rows:
            await self.app.db.executemany("UPDATE invite_links SET revoked = 1 WHERE link = ?",
                                          ((r["link"],) for r in rows))
            for r in rows:
                self.revoke_queue.put_nowait((r["chat_id"], r["link"]))
        return len(rows)

    async def revoke_worker(self) -> None:
        bot = self.app.bot
        while True:
            chat_id, link = await self.revoke_queue.get()
            try:
                await bot.revoke_chat_invite_link(chat_id, link)
            except TelegramRetryAfter as e:
                self.revoke_queue.put_nowait((chat_id, link))
                await asyncio.sleep(e.retry_after + 1)
            except TelegramAPIError:
                pass  # чат недоступен или ссылка уже мертва
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Ошибка отзыва ссылки")
            await asyncio.sleep(0.05)

    async def cleanup(self) -> None:
        t = now()
        await self.revoke("user_id IS NOT NULL AND expires_at IS NOT NULL AND expires_at <= ?", (t,))
        hours = int(self.app.store.setting("cleanup_hours", 24))
        if hours > 0:
            await self.revoke("user_id IS NOT NULL AND assigned_at < ?", (t - hours * 3600,))
        await self.app.db.execute("DELETE FROM invite_links WHERE revoked = 1 AND created_at < ?",
                                  (t - DEAD_KEEP_DAYS * 86400,))

    # ---------- события ----------
    async def register_join(self, chat_id: int, link: str, user_id: int) -> bool:
        """Юзер вступил по нашей ссылке: статистика + ссылку в отзыв, чтобы не висела в чате."""
        db = self.app.db
        row = await db.fetchone("SELECT item_id, chat_id, revoked FROM invite_links WHERE link = ?", (link,))
        if row is None or row["chat_id"] != chat_id:
            return False
        if await db.execute_rowcount(
                "UPDATE invite_links SET used_at = ?, revoked = 1 WHERE link = ? AND used_at IS NULL", (now(), link)):
            await db.execute("INSERT INTO joins(item_id, chat_id, user_id, ts) VALUES (?, ?, ?, ?)",
                             (row["item_id"], chat_id, user_id, now()))
            if not row["revoked"]:
                self.revoke_queue.put_nowait((chat_id, link))
        return True

    async def owner_of(self, link: str) -> int | None:
        """Кому выдана ссылка. None - ссылка не наша."""
        row = await self.app.db.fetchone("SELECT user_id FROM invite_links WHERE link = ?", (link,))
        return None if row is None else (row["user_id"] or 0)

    # ---------- чаты ----------
    async def bind(self, item_id: int, chat_id: int) -> None:
        item = self.app.store.items.get(item_id)
        old_chat = item.chat_id if item else None
        if old_chat and old_chat != chat_id:
            await self.revoke("item_id = ?", (item_id,))
        await self.app.db.execute("UPDATE items SET chat_id = ?, broken = 0, kind = 'invite' WHERE id = ?",
                                  (chat_id, item_id))
        await self.app.reload()
        if old_chat and old_chat != chat_id:
            await self.forget_if_dead(old_chat)
            if item is not None and item.is_active:
                self.app.updates.changed(item_id, item.label)
        self.wake.set()

    async def forget_if_dead(self, chat_id: int) -> None:
        """Мёртвый чат, к которому не привязана ни одна кнопка, убирается из списка подключённых."""
        if await self.app.db.execute_rowcount(
                "DELETE FROM chats WHERE id = ? AND is_present = 0 "
                "AND id NOT IN (SELECT chat_id FROM items WHERE chat_id IS NOT NULL)", (chat_id,)):
            await self.app.reload()

    async def chat_gone(self, chat_id: int, reason: str) -> None:
        """Чат удалён, заморожен или бота там больше нет."""
        await self.app.db.execute("UPDATE chats SET is_present = 0, can_invite = 0, updated_at = ? WHERE id = ?",
                                  (now(), chat_id))
        await self.chat_failed(chat_id, reason)
        await self.forget_if_dead(chat_id)

    async def _touch_link(self, chat_id: int) -> None:
        """Пересохраняет одну живую ссылку бота в чате без изменений: новых ссылок не появляется."""
        row = await self.app.db.fetchone(
            "SELECT link, item_id FROM invite_links WHERE chat_id = ? AND revoked = 0 AND used_at IS NULL LIMIT 1",
            (chat_id,))
        if row is None:
            return
        item = self.app.store.items.get(row["item_id"])
        await self.app.bot.edit_chat_invite_link(chat_id, row["link"], name=item.label[:32] if item else None)

    async def check_chats(self) -> None:
        """Проверка всех чатов без создания новых ссылок: есть ли бот и права, а в чатах с запасом ссылок
        ещё и настоящее действие: пересохранить одну готовую ссылку с тем же названием.
        Так ловятся и удалённые, и замороженные чаты, о которых Telegram ничего не присылает.
        Сбои сети и сервера Telegram чат мёртвым не делают: проверим в следующий раз."""
        bot = self.app.bot
        for chat in list(self.app.store.chats.values()):
            try:
                me = await bot.get_chat_member(chat.id, bot.id)
                if me.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                    await self.chat_gone(chat.id, "бота нет в чате")
                    continue
                if not (isinstance(me, ChatMemberAdministrator) and me.can_invite_users):
                    if chat.can_invite or not chat.is_present:
                        await self.app.db.execute("UPDATE chats SET is_present = 1, can_invite = 0 WHERE id = ?",
                                                  (chat.id,))
                        await self.chat_failed(chat.id, "у бота нет права Приглашать пользователей")
                    continue
                await self._touch_link(chat.id)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                continue
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                await self.chat_gone(chat.id, e.message)
                continue
            except TelegramAPIError:
                continue
            if not (chat.can_invite and chat.is_present):
                await self.app.db.execute("UPDATE chats SET is_present = 1, can_invite = 1 WHERE id = ?", (chat.id,))
                await self.app.reload()
            await self.chat_restored(chat.id)
            await asyncio.sleep(0.3)

    async def chat_failed(self, chat_id: int, reason: str) -> None:
        """Чат недоступен: кнопки помечаются сломанными, админам уведомление (один раз)."""
        store = self.app.store
        newly = [i for i in store.items.values() if i.chat_id == chat_id and not i.broken]
        await self.app.db.execute("UPDATE items SET broken = 1 WHERE chat_id = ?", (chat_id,))
        await self.app.db.execute(
            "UPDATE invite_links SET revoked = 1 WHERE chat_id = ? AND used_at IS NULL AND revoked = 0", (chat_id,))
        await self.app.reload()
        if newly:
            chat = store.chats.get(chat_id)
            title = escape(chat.title if chat else str(chat_id))
            names = ", ".join(f"{escape(i.label)}" for i in newly)
            await self.app.alert(
                f"⚠️ <b>Чат {title} недоступен</b>: удалён, заморожен или у бота нет прав "
                f"({escape(reason)[:150]}).\n"
                f"Не работают кнопки: {names}.\n\n"
                "Замени чат: /admin → 🔗 Кнопки → кнопка → 🔄 Заменить чат.",
                perm="links",
            )

    async def chat_restored(self, chat_id: int) -> None:
        if any(i.chat_id == chat_id and i.broken for i in self.app.store.items.values()):
            await self.app.db.execute("UPDATE items SET broken = 0 WHERE chat_id = ?", (chat_id,))
            await self.app.reload()
        self.wake.set()
