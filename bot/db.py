"""SQLite (WAL) + версионные миграции. На каждую выборку, которую делает бот, есть индекс."""
import logging
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

log = logging.getLogger(__name__)

# Каждая миграция применяется один раз, по порядку. Новые — только добавлять в конец.
MIGRATIONS: list[str] = [
    # 1 — базовая схема
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    ) WITHOUT ROWID;

    CREATE TABLE media (
        id         INTEGER PRIMARY KEY,
        sha256     TEXT NOT NULL UNIQUE,
        path       TEXT NOT NULL,
        kind       TEXT NOT NULL,          -- photo | animation | video
        size       INTEGER NOT NULL,
        created_at INTEGER NOT NULL
    );

    CREATE TABLE media_file_ids (
        media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        bot_id   INTEGER NOT NULL,
        file_id  TEXT NOT NULL,
        PRIMARY KEY (media_id, bot_id)
    ) WITHOUT ROWID;

    -- тексты экранов (HTML с премиум-эмодзи) и системные кнопки
    CREATE TABLE texts (
        key      TEXT PRIMARY KEY,
        html     TEXT NOT NULL DEFAULT '',
        media_id INTEGER REFERENCES media(id) ON DELETE SET NULL
    );
    CREATE TABLE buttons (
        key   TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        icon  TEXT,
        style TEXT
    ) WITHOUT ROWID;

    -- каналы и группы, куда добавлен бот
    CREATE TABLE chats (
        id         INTEGER PRIMARY KEY,
        title      TEXT NOT NULL DEFAULT '',
        type       TEXT NOT NULL,
        username   TEXT,
        can_invite INTEGER NOT NULL DEFAULT 0,  -- админ с правом приглашать
        is_present INTEGER NOT NULL DEFAULT 1,  -- бот в чате
        updated_at INTEGER NOT NULL
    );

    -- кнопки главного меню: invite — личная ссылка в чат, url — обычная ссылка
    CREATE TABLE items (
        id            INTEGER PRIMARY KEY,
        kind          TEXT NOT NULL DEFAULT 'invite',
        label         TEXT NOT NULL,
        icon          TEXT,
        style         TEXT,
        chat_id       INTEGER,
        mode          TEXT NOT NULL DEFAULT 'one_time',   -- one_time | request
        ttl_minutes   INTEGER NOT NULL DEFAULT 0,
        url           TEXT,
        wide          INTEGER NOT NULL DEFAULT 0,          -- во всю ширину
        skip_sponsors INTEGER NOT NULL DEFAULT 0,          -- без обязательной подписки
        is_active     INTEGER NOT NULL DEFAULT 1,
        broken        INTEGER NOT NULL DEFAULT 0,          -- чат недоступен
        position      INTEGER NOT NULL DEFAULT 0,
        created_at    INTEGER NOT NULL
    );
    CREATE INDEX ix_items_chat ON items(chat_id);

    -- инвайт-ссылки. user_id IS NULL — ссылка лежит в пуле готовых и ждёт своего юзера
    CREATE TABLE invite_links (
        link        TEXT PRIMARY KEY,
        item_id     INTEGER NOT NULL,
        chat_id     INTEGER NOT NULL,
        mode        TEXT NOT NULL,
        user_id     INTEGER,
        created_at  INTEGER NOT NULL,
        assigned_at INTEGER,
        expires_at  INTEGER,
        used_at     INTEGER,
        revoked     INTEGER NOT NULL DEFAULT 0
    ) WITHOUT ROWID;
    CREATE INDEX ix_links_pool  ON invite_links(item_id, chat_id, mode, created_at)
        WHERE user_id IS NULL AND revoked = 0;
    CREATE INDEX ix_links_owner ON invite_links(user_id, item_id)
        WHERE user_id IS NOT NULL AND revoked = 0 AND used_at IS NULL;
    CREATE INDEX ix_links_open  ON invite_links(assigned_at)
        WHERE user_id IS NOT NULL AND revoked = 0 AND used_at IS NULL;
    CREATE INDEX ix_links_item  ON invite_links(item_id, assigned_at);
    CREATE INDEX ix_links_dead  ON invite_links(created_at) WHERE revoked = 1;

    CREATE TABLE joins (
        id      INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        ts      INTEGER NOT NULL
    );
    CREATE INDEX ix_joins_item_ts ON joins(item_id, ts);
    CREATE INDEX ix_joins_ts ON joins(ts);

    CREATE TABLE users (
        id            INTEGER PRIMARY KEY,
        username      TEXT,
        first_name    TEXT,
        created_at    INTEGER NOT NULL,
        last_seen     INTEGER NOT NULL,
        is_banned     INTEGER NOT NULL DEFAULT 0,
        banned_until  INTEGER,
        ban_reason    TEXT,
        is_blocked    INTEGER NOT NULL DEFAULT 0,
        screen_msg_id INTEGER
    );
    CREATE INDEX ix_users_banned   ON users(is_banned) WHERE is_banned = 1;
    CREATE INDEX ix_users_blocked  ON users(is_blocked) WHERE is_blocked = 1;
    CREATE INDEX ix_users_last_seen ON users(last_seen);
    CREATE INDEX ix_users_created  ON users(created_at);
    CREATE INDEX ix_users_username ON users(username COLLATE NOCASE);

    CREATE TABLE admins (
        user_id    INTEGER PRIMARY KEY,
        perms      TEXT NOT NULL DEFAULT '',
        added_by   INTEGER,
        created_at INTEGER NOT NULL
    );

    CREATE TABLE admin_log (
        id       INTEGER PRIMARY KEY,
        admin_id INTEGER NOT NULL,
        action   TEXT NOT NULL,
        details  TEXT NOT NULL DEFAULT '',
        ts       INTEGER NOT NULL
    );
    CREATE INDEX ix_admin_log_ts ON admin_log(ts);

    -- реклама
    CREATE TABLE ads (
        id            INTEGER PRIMARY KEY,
        title         TEXT NOT NULL,
        html          TEXT NOT NULL DEFAULT '',
        media_id      INTEGER REFERENCES media(id) ON DELETE SET NULL,
        buttons       TEXT NOT NULL DEFAULT '[]',        -- JSON [[label, url, icon, style], ...]
        status        TEXT NOT NULL DEFAULT 'draft',     -- draft | active | paused | finished
        placements    TEXT NOT NULL DEFAULT 'start,link',
        max_views     INTEGER NOT NULL DEFAULT 0,        -- 0 — без лимита
        freq_hours    INTEGER NOT NULL DEFAULT 0,        -- 0 — каждый раз, -1 — один раз на человека
        ends_at       INTEGER,
        views         INTEGER NOT NULL DEFAULT 0,
        uniques       INTEGER NOT NULL DEFAULT 0,
        created_at    INTEGER NOT NULL,
        started_at    INTEGER,
        finished_at   INTEGER,
        finish_reason TEXT
    );
    CREATE INDEX ix_ads_status ON ads(status);
    CREATE TABLE ad_users (
        user_id INTEGER NOT NULL,
        ad_id   INTEGER NOT NULL,
        last_ts INTEGER NOT NULL,
        views   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, ad_id)
    ) WITHOUT ROWID;
    CREATE INDEX ix_ad_users_ad ON ad_users(ad_id);
    CREATE TABLE ad_daily (
        ad_id   INTEGER NOT NULL,
        day     TEXT NOT NULL,
        views   INTEGER NOT NULL DEFAULT 0,
        uniques INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (ad_id, day)
    ) WITHOUT ROWID;

    -- спонсоры (обязательная подписка)
    CREATE TABLE sponsors (
        id            INTEGER PRIMARY KEY,
        chat_id       INTEGER NOT NULL,
        title         TEXT NOT NULL DEFAULT '',
        url           TEXT NOT NULL DEFAULT '',
        link_mode     TEXT NOT NULL DEFAULT 'plain',     -- plain | request (заявка = подписка)
        target        INTEGER NOT NULL DEFAULT 0,        -- 0 — без лимита
        joins         INTEGER NOT NULL DEFAULT 0,
        requests      INTEGER NOT NULL DEFAULT 0,
        ends_at       INTEGER,
        is_active     INTEGER NOT NULL DEFAULT 1,
        position      INTEGER NOT NULL DEFAULT 0,
        created_at    INTEGER NOT NULL,
        finished_at   INTEGER,
        finish_reason TEXT
    );
    CREATE INDEX ix_sponsors_chat ON sponsors(chat_id);
    CREATE TABLE sponsor_users (
        sponsor_id INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        kind       TEXT NOT NULL,                        -- join | request
        ts         INTEGER NOT NULL,
        PRIMARY KEY (sponsor_id, user_id, kind)
    ) WITHOUT ROWID;
    CREATE INDEX ix_sponsor_users_user ON sponsor_users(user_id);
    """,
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "database is not connected"
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        for pragma in (
            "journal_mode=WAL",
            "synchronous=NORMAL",
            "foreign_keys=ON",
            "temp_store=MEMORY",
            "cache_size=-16000",  # ~16 МБ страничного кэша
            "mmap_size=134217728",
            "busy_timeout=5000",
        ):
            await self._conn.execute(f"PRAGMA {pragma}")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.execute("PRAGMA optimize")
            finally:
                await self._conn.close()
                self._conn = None

    async def _migrate(self) -> None:
        version = (await self.fetchval("PRAGMA user_version")) or 0
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            log.info("Применяю миграцию %s", number)
            await self.conn.executescript(f"BEGIN;{sql}\nPRAGMA user_version={number};COMMIT;")

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchval(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row is not None else None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Выполнить запрос и закоммитить. Возвращает lastrowid."""
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur.lastrowid or 0

    async def returning(self, sql: str, params: Iterable[Any] = ()) -> Any:
        """INSERT/UPDATE … RETURNING x: первое значение первой строки, с коммитом."""
        async with self.conn.execute(sql, tuple(params)) as cur:
            row = await cur.fetchone()
        await self.conn.commit()
        return row[0] if row is not None else None

    async def execute_rowcount(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur.rowcount

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        await self.conn.executemany(sql, [tuple(r) for r in rows])
        await self.conn.commit()

    async def backup_to(self, target: Path) -> None:
        """Консистентная копия базы (работает на живой базе)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(target) as dst:
            await self.conn.backup(dst)
