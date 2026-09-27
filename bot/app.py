"""Общий контейнер сервисов и буферы записи (чтобы не дёргать базу на каждый клик)."""
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

from .config import Config
from .db import Database
from .media import MediaStore
from .store import Store, now

if TYPE_CHECKING:
    from .services.ads import AdService
    from .services.broadcast import Broadcaster
    from .services.links import LinkService

log = logging.getLogger(__name__)

BIND_WAIT = 10 * 60


@dataclass(slots=True)
class Screen:
    """Где у пользователя висит текущий экран бота."""
    message_id: int
    has_media: bool


@dataclass(slots=True)
class PendingBind:
    """Админ нажал Привязать чат и сейчас добавит бота в канал/группу."""
    kind: str      # item
    ref: int       # id кнопки
    until: int


@dataclass
class App:
    config: Config
    db: Database
    store: Store
    media: MediaStore
    bot: Bot
    bot_username: str = ""
    links: "LinkService" = None  # type: ignore[assignment]
    ads: "AdService" = None  # type: ignore[assignment]
    broadcaster: "Broadcaster" = None  # type: ignore[assignment]
    known_users: set[int] = field(default_factory=set)
    banned: dict[int, int | None] = field(default_factory=dict)  # user_id -> until (None = навсегда)
    screens: dict[int, Screen] = field(default_factory=dict)
    pending_binds: dict[int, PendingBind] = field(default_factory=dict)
    _seen: dict[int, tuple[int, str | None, str | None]] = field(default_factory=dict)
    _screen_ids: dict[int, int] = field(default_factory=dict)

    async def load_users(self) -> None:
        self.known_users = {r["id"] for r in await self.db.fetchall("SELECT id FROM users")}
        self.banned = {r["id"]: r["banned_until"]
                       for r in await self.db.fetchall("SELECT id, banned_until FROM users WHERE is_banned = 1")}

    async def reload(self) -> None:
        """Перечитать кэш. Сначала сбрасываем буферы - иначе живые счётчики затрутся старыми из базы."""
        await self.flush()
        await self.store.reload()

    async def set_setting(self, key: str, value: Any) -> None:
        await self.db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.store.settings[key] = value

    # ---------- пользователи ----------
    async def touch_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        ts = now()
        if user_id not in self.known_users:
            self.known_users.add(user_id)
            await self.db.execute(
                "INSERT OR IGNORE INTO users(id, username, first_name, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, first_name, ts, ts),
            )
            return
        self._seen[user_id] = (ts, username, first_name)

    async def mark_blocked(self, user_id: int, blocked: bool = True) -> None:
        await self.db.execute("UPDATE users SET is_blocked = ? WHERE id = ?", (int(blocked), user_id))

    def is_banned(self, user_id: int) -> bool:
        if user_id not in self.banned:
            return False
        until = self.banned[user_id]
        return until is None or until > now()

    async def ban(self, user_id: int, until: int | None, reason: str) -> None:
        if user_id not in self.known_users:
            self.known_users.add(user_id)
            await self.db.execute("INSERT OR IGNORE INTO users(id, created_at, last_seen) VALUES (?, ?, ?)",
                                  (user_id, now(), now()))
        self.banned[user_id] = until
        await self.db.execute("UPDATE users SET is_banned = 1, banned_until = ?, ban_reason = ? WHERE id = ?",
                              (until, reason, user_id))

    async def unban(self, user_id: int) -> None:
        self.banned.pop(user_id, None)
        await self.db.execute(
            "UPDATE users SET is_banned = 0, banned_until = NULL, ban_reason = NULL WHERE id = ?", (user_id,))

    # ---------- экраны ----------
    def set_screen(self, user_id: int, message_id: int, has_media: bool) -> None:
        self.screens[user_id] = Screen(message_id, has_media)
        self._screen_ids[user_id] = message_id

    async def last_screen_id(self, user_id: int) -> int | None:
        """Id прошлого экрана - из памяти или (после перезапуска) из базы."""
        if user_id in self.screens:
            return self.screens[user_id].message_id
        return await self.db.fetchval("SELECT screen_msg_id FROM users WHERE id = ?", (user_id,))

    # ---------- привязка чатов ----------
    def start_bind(self, admin_id: int, kind: str, ref: int = 0) -> None:
        self.pending_binds[admin_id] = PendingBind(kind, ref, now() + BIND_WAIT)

    def pending_bind(self, admin_id: int) -> PendingBind | None:
        p = self.pending_binds.get(admin_id)
        if p is None or p.until < now():
            self.pending_binds.pop(admin_id, None)
            return None
        return p

    # ---------- буферы ----------
    async def flush(self) -> None:
        """Сбрасывает накопленные записи в базу одной пачкой."""
        seen, self._seen = self._seen, {}
        screen_ids, self._screen_ids = self._screen_ids, {}
        if seen:
            await self.db.executemany(
                "UPDATE users SET last_seen = ?, username = ?, first_name = ?, is_blocked = 0 WHERE id = ?",
                ((ts, un, fn, uid) for uid, (ts, un, fn) in seen.items()),
            )
        if screen_ids:
            await self.db.executemany("UPDATE users SET screen_msg_id = ? WHERE id = ?",
                                      ((mid, uid) for uid, mid in screen_ids.items()))
        if self.ads is not None:
            await self.ads.flush()

    # ---------- админы ----------
    def admin_ids(self, perm: str | None = None) -> list[int]:
        ids = set(self.config.owner_ids)
        for uid, perms in self.store.admins.items():
            if perm is None or perm in perms:
                ids.add(uid)
        return sorted(ids)

    def perms(self, user_id: int) -> set[str] | None:
        return self.store.perms_of(user_id, self.config.owner_ids)

    def has_perm(self, user_id: int, perm: str) -> bool:
        perms = self.perms(user_id)
        return perms is not None and ("*" in perms or perm in perms)

    async def notify_admins(self, html: str, perm: str | None = None, **kwargs) -> None:
        for uid in self.admin_ids(perm):
            try:
                await self.bot.send_message(uid, html, **kwargs)
            except TelegramForbiddenError:
                pass
            except TelegramAPIError as e:
                log.warning("Не удалось уведомить админа %s: %s", uid, e)

    async def alert(self, html: str, perm: str | None = None, **kwargs) -> None:
        """Важное событие: админам в личку и в канал логов."""
        await self.notify_admins(html, perm, **kwargs)
        await self.log_event(html)

    async def log_action(self, admin_id: int, action: str, details: str = "", pretty: str = "") -> None:
        await self.db.execute("INSERT INTO admin_log(admin_id, action, details, ts) VALUES (?, ?, ?, ?)",
                              (admin_id, action, details[:500], now()))
        if pretty and self.store.setting("log_admin_actions", 1):
            await self.log_event(f"📜 {pretty}")

    # ---------- канал логов ----------
    @property
    def log_chat(self) -> int:
        return int(self.store.setting("log_chat_id", 0) or 0)

    async def log_event(self, html: str, **kwargs) -> bool:
        chat = self.log_chat
        if not chat:
            return False
        try:
            await self.bot.send_message(chat, html[:4000], disable_notification=True, **kwargs)
            return True
        except TelegramAPIError as e:
            log.warning("Канал логов недоступен: %s", e)
            return False
