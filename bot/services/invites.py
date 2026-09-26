"""Выдача, учёт и отзыв инвайт-ссылок, привязка кнопок к чатам."""
import asyncio
import logging

import aiosqlite
from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from bot.db import Database, now

log = logging.getLogger(__name__)

CLEANUP_EVERY = 10 * 60
DEFAULT_CLEANUP_HOURS = 24


class LinkUnavailable(Exception):
    """Чат недоступен: бота выкинули, отобрали права или чат удалён."""


class LinkTemporaryError(Exception):
    """Флуд-контроль или сетевая ошибка — стоит повторить позже."""


async def is_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    if m.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(m, "is_member", False))
    return m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


async def get_or_create_link(bot: Bot, db: Database, button: aiosqlite.Row, user_id: int) -> aiosqlite.Row:
    """Отдаёт живую ссылку юзера для кнопки; новую создаёт только если старой нет."""
    ts = now()
    existing = await db.fetchone(
        "SELECT * FROM invite_links WHERE user_id=? AND button_id=? AND chat_id=? AND mode=? "
        "AND revoked=0 AND used_at IS NULL AND (expires_at IS NULL OR expires_at>?) "
        "ORDER BY created_at DESC LIMIT 1",
        user_id, button["id"], button["chat_id"], button["mode"], ts + 60,
    )
    if existing:
        return existing

    expires_at = ts + button["ttl_minutes"] * 60 if button["ttl_minutes"] else None
    kwargs = {"chat_id": button["chat_id"], "name": f"u{user_id}", "expire_date": expires_at}
    if button["mode"] == "request":
        kwargs["creates_join_request"] = True
    else:
        kwargs["member_limit"] = 1
    try:
        invite = await bot.create_chat_invite_link(**kwargs)
    except TelegramRetryAfter as e:
        raise LinkTemporaryError(str(e)) from e
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        raise LinkUnavailable(str(e)) from e
    except TelegramAPIError as e:
        raise LinkTemporaryError(str(e)) from e

    await db.execute(
        "INSERT INTO invite_links(link, button_id, chat_id, user_id, mode, created_at, expires_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        invite.invite_link, button["id"], button["chat_id"], user_id, button["mode"], ts, expires_at,
    )
    return await db.fetchone("SELECT * FROM invite_links WHERE link=?", invite.invite_link)


async def revoke_one(bot: Bot, db: Database, chat_id: int, link: str) -> None:
    for _ in range(2):
        try:
            await bot.revoke_chat_invite_link(chat_id, link)
            break
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramAPIError:
            break  # чат недоступен или ссылка уже мертва — просто забываем её
    await db.execute("UPDATE invite_links SET revoked=1 WHERE link=?", link)


async def revoke_links(bot: Bot, db: Database, where: str, *args) -> int:
    """Отзывает все неиспользованные ссылки по условию. Возвращает количество."""
    rows = await db.fetchall(
        f"SELECT link, chat_id FROM invite_links WHERE revoked=0 AND used_at IS NULL AND ({where})", *args
    )
    for row in rows:
        await revoke_one(bot, db, row["chat_id"], row["link"])
        await asyncio.sleep(0.05)
    return len(rows)


async def register_join(bot: Bot, db: Database, link: str, user_id: int, chat_id: int) -> bool:
    """Юзер вступил по ссылке: пишем статистику и отзываем ссылку, чтобы не висела в чате."""
    row = await db.fetchone("SELECT * FROM invite_links WHERE link=?", link)
    if row is None or row["chat_id"] != chat_id:
        return False
    cur = await db.execute(
        "UPDATE invite_links SET used_at=? WHERE link=? AND used_at IS NULL", now(), link
    )
    if cur.rowcount:
        await db.execute(
            "INSERT INTO joins(button_id, chat_id, user_id, at) VALUES(?, ?, ?, ?)",
            row["button_id"], chat_id, user_id, now(),
        )
    if not row["revoked"]:
        await revoke_one(bot, db, chat_id, link)
    return True


async def bind_button(bot: Bot, db: Database, button_id: int, chat_id: int) -> None:
    button = await db.get_button(button_id)
    if button["chat_id"] is not None and button["chat_id"] != chat_id:
        await revoke_links(bot, db, "button_id=?", button_id)
    await db.update_button(button_id, chat_id=chat_id, broken=0)


async def mark_chat_broken(db: Database, chat_id: int) -> list[aiosqlite.Row]:
    """Помечает кнопки чата сломанными. Возвращает те, что сломались только что."""
    rows = await db.fetchall("SELECT * FROM buttons WHERE chat_id=? AND broken=0", chat_id)
    await db.execute("UPDATE buttons SET broken=1 WHERE chat_id=?", chat_id)
    await db.execute("UPDATE invite_links SET revoked=1 WHERE chat_id=? AND used_at IS NULL", chat_id)
    return rows


async def cleanup_loop(bot: Bot, db: Database) -> None:
    """Фоном отзывает протухшие и давно не использованные ссылки."""
    while True:
        await asyncio.sleep(CLEANUP_EVERY)
        try:
            hours = await db.get_setting("cleanup_hours", DEFAULT_CLEANUP_HOURS)
            if hours:
                ts = now()
                n = await revoke_links(
                    bot, db, "created_at<? OR (expires_at IS NOT NULL AND expires_at<?)",
                    ts - hours * 3600, ts,
                )
                if n:
                    log.info("cleanup: revoked %s links", n)
        except Exception:
            log.exception("cleanup failed")
