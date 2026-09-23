import json
import sqlite3
from datetime import date, datetime, timedelta

from config import DB_PATH, DEFAULT_GROUP_ID

_conn: sqlite3.Connection | None = None

# Тексты для пользователя. Меняются в админ-панели без перезапуска.
# В тексте можно использовать HTML-разметку Telegram.
DEFAULT_TEXTS: dict[str, str] = {
    "start": "👋 Здравствуйте, {name}!\n\nНапишите ваш вопрос — мы ответим прямо здесь.",
    "sent": "",
    "only_text": "⚠️ Принимаются только текстовые сообщения.",
    "flood": "⏳ Слишком много сообщений. Подождите {seconds} сек.",
    "duplicate": "⚠️ Вы уже отправили это сообщение.",
    "too_long": "⚠️ Сообщение слишком длинное (максимум {max} символов).",
    "banned": "",
    "unavailable": "⚠️ Сервис временно недоступен, попробуйте позже.",
    "reply": "💬 Ответил {who}:\n\n{text}",
}

# Подписи и подсказки для админ-панели.
TEXT_LABELS: dict[str, tuple[str, str]] = {
    "start": ("👋 Приветствие", "Ответ на /start. Переменная {name} — имя пользователя."),
    "sent": ("📨 Подтверждение", "После пересылки сообщения. Пусто — не отправлять."),
    "only_text": ("🖼 Только текст", "Когда прислали фото, стикер, голосовое и т.п."),
    "flood": ("⏳ Флуд", "Когда сработал антифлуд. {seconds} — сколько ждать."),
    "duplicate": ("🔁 Повтор", "Одинаковое сообщение подряд."),
    "too_long": ("📏 Длинное", "Слишком длинное сообщение. {max} — лимит."),
    "banned": ("🚫 Бан", "Заблокированному пользователю. Пусто — молча игнорировать."),
    "unavailable": ("⚠️ Недоступно", "Группа не настроена или бот не может в неё писать."),
    "reply": ("💬 Формат ответа", "{who} — @username или ID ответившего, {text} — ответ."),
}

# Антифлуд/антиспам (действует только на пользователей в ЛС).
DEFAULT_SETTINGS: dict[str, int] = {
    "flood_limit": 5,
    "flood_window": 10,
    "flood_mute": 30,
    "duplicate_window": 60,
    "max_length": 3500,
}

SETTING_LABELS: dict[str, str] = {
    "flood_limit": "Сообщений за окно (0 — без лимита)",
    "flood_window": "Окно антифлуда, сек",
    "flood_mute": "Мут за флуд, сек",
    "duplicate_window": "Повтор текста, сек (0 — выкл)",
    "max_length": "Макс. длина (0 — без лимита)",
}

AD_SLOTS = 10
AD_MODES = ("every_start", "once_ever", "interval_hours", "times_per_day")


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=5000")
    return _conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            is_banned INTEGER DEFAULT 0
        );

        -- Какое сообщение в группе относится к какому пользователю:
        -- по реплаю на него ответ уходит нужному человеку.
        CREATE TABLE IF NOT EXISTS message_map (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        );

        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            messages INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0
        );

        -- Рекламные слоты. content/buttons — JSON.
        CREATE TABLE IF NOT EXISTS ads (
            slot_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            content TEXT,
            buttons TEXT,
            mode TEXT DEFAULT 'every_start',
            value INTEGER DEFAULT 1
        );

        -- Когда и сколько раз слот показан пользователю (для расписания).
        CREATE TABLE IF NOT EXISTS ad_shows (
            user_id INTEGER,
            slot_id INTEGER,
            shown_date TEXT,
            shown_count INTEGER DEFAULT 0,
            last_shown_at TEXT,
            PRIMARY KEY (user_id, slot_id)
        );
        """
    )
    _migrate(conn)
    conn.executescript(
        """
        -- Статистика: новые/активные за период.
        CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen_at);
        CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen_at);
        -- Частичный индекс: забаненных мало, индекс крошечный.
        CREATE INDEX IF NOT EXISTS idx_users_banned ON users(user_id) WHERE is_banned = 1;
        -- Все сообщения конкретного пользователя в группе.
        CREATE INDEX IF NOT EXISTS idx_message_map_user ON message_map(user_id);
        -- Сброс показов слота при смене рекламы.
        CREATE INDEX IF NOT EXISTS idx_ad_shows_slot ON ad_shows(slot_id);
        -- Выбор включённых слотов при каждом сообщении пользователя.
        CREATE INDEX IF NOT EXISTS idx_ads_enabled ON ads(slot_id) WHERE enabled = 1;
        """
    )
    conn.commit()
    if DEFAULT_GROUP_ID and get_group_id() == 0:
        set_group_id(DEFAULT_GROUP_ID)


def _migrate(conn: sqlite3.Connection) -> None:
    """Досоздаёт колонки, если bot.db остался от старой версии бота."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in (
        ("first_name", "TEXT"),
        ("first_seen_at", "TEXT"),
        ("last_seen_at", "TEXT"),
        ("is_banned", "INTEGER DEFAULT 0"),
    ):
        if column not in have:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
    have = {r["name"] for r in conn.execute("PRAGMA table_info(daily_stats)")}
    for column in ("messages", "replies"):
        if column not in have:
            conn.execute(f"ALTER TABLE daily_stats ADD COLUMN {column} INTEGER DEFAULT 0")


# ---------- key-value ----------

def _kv_get(key: str) -> str | None:
    row = get_conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _kv_set(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def _kv_del(key: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM kv WHERE key = ?", (key,))
    conn.commit()


def get_group_id() -> int:
    return int(_kv_get("group_id") or 0)


def set_group_id(chat_id: int) -> None:
    _kv_set("group_id", str(chat_id))


def get_text(key: str) -> str:
    value = _kv_get(f"text:{key}")
    return value if value is not None else DEFAULT_TEXTS.get(key, "")


def set_text(key: str, value: str) -> None:
    _kv_set(f"text:{key}", value)


def reset_text(key: str) -> None:
    _kv_del(f"text:{key}")


def get_setting(key: str) -> int:
    value = _kv_get(f"setting:{key}")
    return int(value) if value is not None else DEFAULT_SETTINGS[key]


def set_setting(key: str, value: int) -> None:
    _kv_set(f"setting:{key}", str(value))


def ads_enabled() -> bool:
    return _kv_get("ads_enabled") != "0"


def set_ads_enabled(enabled: bool) -> None:
    _kv_set("ads_enabled", "1" if enabled else "0")


# ---------- users ----------

def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO users (user_id, username, first_name, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, "
        "first_name = excluded.first_name, last_seen_at = excluded.last_seen_at, "
        "first_seen_at = COALESCE(users.first_seen_at, excluded.first_seen_at)",
        (user_id, username, first_name, now, now),
    )
    conn.commit()


def get_user(user_id: int) -> dict | None:
    row = get_conn().execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def is_banned(user_id: int) -> bool:
    row = get_conn().execute(
        "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return bool(row and row["is_banned"])


def set_banned(user_id: int, banned: bool) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (user_id, is_banned) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET is_banned = excluded.is_banned",
        (user_id, int(banned)),
    )
    conn.commit()


def banned_users(limit: int = 30) -> list[dict]:
    rows = get_conn().execute(
        "SELECT user_id, username, first_name FROM users WHERE is_banned = 1 "
        "ORDER BY user_id LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- статистика ----------

def bump_stat(field: str) -> None:
    """field: 'messages' | 'replies'"""
    conn = get_conn()
    conn.execute(
        f"INSERT INTO daily_stats (date, {field}) VALUES (?, 1) "
        f"ON CONFLICT(date) DO UPDATE SET {field} = {field} + 1",
        (date.today().isoformat(),),
    )
    conn.commit()


def get_stats() -> dict:
    conn = get_conn()
    today = date.today()

    def since(days: int) -> str:
        return (today - timedelta(days=days)).isoformat()

    def count(sql: str, *args) -> int:
        return conn.execute(sql, args).fetchone()[0]

    def msgs(days: int) -> tuple[int, int]:
        row = conn.execute(
            "SELECT COALESCE(SUM(messages), 0), COALESCE(SUM(replies), 0) "
            "FROM daily_stats WHERE date >= ?",
            (since(days),),
        ).fetchone()
        return row[0], row[1]

    return {
        "total": count("SELECT COUNT(*) FROM users"),
        "banned": count("SELECT COUNT(*) FROM users WHERE is_banned = 1"),
        "new_today": count("SELECT COUNT(*) FROM users WHERE first_seen_at >= ?", since(0)),
        "new_week": count("SELECT COUNT(*) FROM users WHERE first_seen_at >= ?", since(7)),
        "new_month": count("SELECT COUNT(*) FROM users WHERE first_seen_at >= ?", since(30)),
        "active_today": count("SELECT COUNT(*) FROM users WHERE last_seen_at >= ?", since(0)),
        "active_week": count("SELECT COUNT(*) FROM users WHERE last_seen_at >= ?", since(7)),
        "msgs_today": msgs(0),
        "msgs_week": msgs(7),
    }


# ---------- message map ----------

def map_message(chat_id: int, message_id: int, user_id: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO message_map (chat_id, message_id, user_id) VALUES (?, ?, ?)",
        (chat_id, message_id, user_id),
    )
    conn.commit()


def find_user_by_message(chat_id: int, message_id: int) -> int | None:
    row = get_conn().execute(
        "SELECT user_id FROM message_map WHERE chat_id = ? AND message_id = ?",
        (chat_id, message_id),
    ).fetchone()
    return row["user_id"] if row else None


# ---------- admins ----------

def list_admins() -> set[int]:
    rows = get_conn().execute("SELECT user_id FROM admins").fetchall()
    return {r["user_id"] for r in rows}


def add_admin(user_id: int) -> None:
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()


def remove_admin(user_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()


# ---------- реклама ----------

def _ad_from_row(slot_id: int, row: sqlite3.Row | None) -> dict:
    if row is None:
        return {"slot_id": slot_id, "enabled": False, "content": None, "buttons": [],
                "mode": "every_start", "value": 1}
    return {
        "slot_id": slot_id,
        "enabled": bool(row["enabled"]),
        "content": json.loads(row["content"]) if row["content"] else None,
        "buttons": json.loads(row["buttons"]) if row["buttons"] else [],
        "mode": row["mode"] or "every_start",
        "value": row["value"] or 1,
    }


def get_ad(slot_id: int) -> dict:
    row = get_conn().execute("SELECT * FROM ads WHERE slot_id = ?", (slot_id,)).fetchone()
    return _ad_from_row(slot_id, row)


def list_ads() -> list[dict]:
    rows = {r["slot_id"]: r for r in get_conn().execute("SELECT * FROM ads")}
    return [_ad_from_row(i, rows.get(i)) for i in range(1, AD_SLOTS + 1)]


def enabled_ads() -> list[dict]:
    rows = get_conn().execute(
        "SELECT * FROM ads WHERE enabled = 1 ORDER BY slot_id"
    ).fetchall()
    return [_ad_from_row(r["slot_id"], r) for r in rows]


def update_ad(slot_id: int, **fields) -> None:
    """fields: enabled, content, buttons, mode, value."""
    if "content" in fields:
        fields["content"] = json.dumps(fields["content"], ensure_ascii=False) if fields["content"] else None
    if "buttons" in fields:
        fields["buttons"] = json.dumps(fields["buttons"], ensure_ascii=False)
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO ads (slot_id) VALUES (?)", (slot_id,))
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE ads SET {sets} WHERE slot_id = ?", (*fields.values(), slot_id))
    conn.commit()


def reset_ad_shows(slot_id: int) -> None:
    """Новая реклама в слоте — показываем её всем заново."""
    conn = get_conn()
    conn.execute("DELETE FROM ad_shows WHERE slot_id = ?", (slot_id,))
    conn.commit()


def get_ad_show(user_id: int, slot_id: int) -> dict:
    row = get_conn().execute(
        "SELECT * FROM ad_shows WHERE user_id = ? AND slot_id = ?", (user_id, slot_id)
    ).fetchone()
    if not row:
        return {"shown_count": 0, "last_shown_at": None}
    data = dict(row)
    # Дневной счётчик обнуляется в новый день, last_shown_at храним как есть.
    if data["shown_date"] != date.today().isoformat():
        data["shown_count"] = 0
    return data


def register_ad_show(user_id: int, slot_id: int) -> None:
    conn = get_conn()
    today = date.today().isoformat()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO ad_shows (user_id, slot_id, shown_date, shown_count, last_shown_at) "
        "VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(user_id, slot_id) DO UPDATE SET "
        "shown_count = CASE WHEN ad_shows.shown_date = excluded.shown_date "
        "THEN ad_shows.shown_count + 1 ELSE 1 END, "
        "shown_date = excluded.shown_date, last_shown_at = excluded.last_shown_at",
        (user_id, slot_id, today, now),
    )
    conn.commit()
