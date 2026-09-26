import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    first_name  TEXT,
    username    TEXT,
    created_at  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    blocked     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS admins (
    id        INTEGER PRIMARY KEY,
    added_at  INTEGER NOT NULL
);
-- Чаты и каналы, куда добавлен бот. can_invite = бот админ с правом приглашать.
CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY,
    title       TEXT,
    type        TEXT,
    can_invite  INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL
);
-- Кнопка меню живёт отдельно от чата: чат можно заменить, кнопка останется.
CREATE TABLE IF NOT EXISTS buttons (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    chat_id      INTEGER,
    mode         TEXT NOT NULL DEFAULT 'one_time',
    ttl_minutes  INTEGER NOT NULL DEFAULT 0,
    enabled      INTEGER NOT NULL DEFAULT 1,
    broken       INTEGER NOT NULL DEFAULT 0,
    position     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS invite_links (
    link        TEXT PRIMARY KEY,
    button_id   INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    mode        TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER,
    used_at     INTEGER,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_links_owner ON invite_links(user_id, button_id);
CREATE INDEX IF NOT EXISTS idx_links_open ON invite_links(revoked, used_at);
CREATE TABLE IF NOT EXISTS joins (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    button_id  INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    at         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
-- Админ нажал «Привязать чат» и ждёт, пока добавит бота в новый чат.
CREATE TABLE IF NOT EXISTS pending_binds (
    admin_id   INTEGER PRIMARY KEY,
    button_id  INTEGER NOT NULL,
    until      INTEGER NOT NULL
);
"""

BIND_WAIT_SECONDS = 10 * 60


def now() -> int:
    return int(time.time())


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def connect(cls, path: str) -> "Database":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()

    # --- низкоуровневые ---

    async def execute(self, sql: str, *args: Any) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur

    async def fetchone(self, sql: str, *args: Any) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, args) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, *args: Any) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, args) as cur:
            return list(await cur.fetchall())

    async def scalar(self, sql: str, *args: Any) -> Any:
        row = await self.fetchone(sql, *args)
        return row[0] if row else None

    # --- настройки ---

    async def get_setting(self, key: str, default: Any = None) -> Any:
        raw = await self.scalar("SELECT value FROM settings WHERE key=?", key)
        return default if raw is None else json.loads(raw)

    async def set_setting(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            key, json.dumps(value, ensure_ascii=False),
        )

    async def delete_setting(self, key: str) -> None:
        await self.execute("DELETE FROM settings WHERE key=?", key)

    # --- пользователи ---

    async def touch_user(self, user_id: int, first_name: str | None, username: str | None) -> None:
        ts = now()
        await self.execute(
            "INSERT INTO users(id, first_name, username, created_at, last_seen) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET first_name=excluded.first_name, "
            "username=excluded.username, last_seen=excluded.last_seen, blocked=0",
            user_id, first_name, username, ts, ts,
        )

    async def set_blocked(self, user_id: int, blocked: bool) -> None:
        await self.execute("UPDATE users SET blocked=? WHERE id=?", int(blocked), user_id)

    # --- админы ---

    async def is_admin(self, user_id: int) -> bool:
        return await self.scalar("SELECT 1 FROM admins WHERE id=?", user_id) is not None

    async def list_admins(self) -> list[int]:
        return [r["id"] for r in await self.fetchall("SELECT id FROM admins ORDER BY added_at")]

    async def add_admin(self, user_id: int) -> None:
        await self.execute("INSERT OR IGNORE INTO admins(id, added_at) VALUES(?, ?)", user_id, now())

    async def remove_admin(self, user_id: int) -> None:
        await self.execute("DELETE FROM admins WHERE id=?", user_id)

    # --- чаты ---

    async def upsert_chat(self, chat_id: int, title: str | None, type_: str, can_invite: bool) -> None:
        await self.execute(
            "INSERT INTO chats(id, title, type, can_invite, updated_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, type=excluded.type, "
            "can_invite=excluded.can_invite, updated_at=excluded.updated_at",
            chat_id, title, type_, int(can_invite), now(),
        )

    async def get_chat(self, chat_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM chats WHERE id=?", chat_id)

    async def list_invite_chats(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM chats WHERE can_invite=1 ORDER BY updated_at DESC")

    # --- кнопки ---

    async def list_buttons(self, only_enabled: bool = False) -> list[aiosqlite.Row]:
        where = "WHERE enabled=1" if only_enabled else ""
        return await self.fetchall(f"SELECT * FROM buttons {where} ORDER BY position, id")

    async def get_button(self, button_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM buttons WHERE id=?", button_id)

    async def create_button(self, title: str) -> int:
        pos = await self.scalar("SELECT COALESCE(MAX(position), 0) + 1 FROM buttons")
        cur = await self.execute("INSERT INTO buttons(title, position) VALUES(?, ?)", title, pos)
        return cur.lastrowid

    _BUTTON_FIELDS = {"title", "chat_id", "mode", "ttl_minutes", "enabled", "broken"}

    async def update_button(self, button_id: int, **fields: Any) -> None:
        assert fields and set(fields) <= self._BUTTON_FIELDS, fields
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE buttons SET {cols} WHERE id=?", *fields.values(), button_id)

    async def delete_button(self, button_id: int) -> None:
        await self.execute("DELETE FROM buttons WHERE id=?", button_id)

    async def move_button(self, button_id: int, delta: int) -> None:
        ids = [r["id"] for r in await self.list_buttons()]
        i = ids.index(button_id)
        j = i + delta
        if not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        await self.conn.executemany(
            "UPDATE buttons SET position=? WHERE id=?", [(p, bid) for p, bid in enumerate(ids)]
        )
        await self.conn.commit()

    # --- ожидание привязки ---

    async def set_pending_bind(self, admin_id: int, button_id: int) -> None:
        await self.execute(
            "INSERT INTO pending_binds(admin_id, button_id, until) VALUES(?, ?, ?) "
            "ON CONFLICT(admin_id) DO UPDATE SET button_id=excluded.button_id, until=excluded.until",
            admin_id, button_id, now() + BIND_WAIT_SECONDS,
        )

    async def get_pending_bind(self, admin_id: int) -> int | None:
        return await self.scalar(
            "SELECT button_id FROM pending_binds WHERE admin_id=? AND until>?", admin_id, now()
        )

    async def clear_pending_bind(self, admin_id: int) -> None:
        await self.execute("DELETE FROM pending_binds WHERE admin_id=?", admin_id)
