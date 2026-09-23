import sqlite3
from datetime import datetime

from config import DB_PATH, DEFAULT_GROUP_ID

_conn: sqlite3.Connection | None = None

# Тексты для пользователя. Все меняются командой /settext без перезапуска.
# В тексте можно использовать HTML-разметку Telegram.
DEFAULT_TEXTS: dict[str, str] = {
    # Ответ на /start. {name} — имя пользователя.
    "start": "👋 Здравствуйте, {name}!\n\nНапишите ваш вопрос — мы ответим прямо здесь.",
    # Подтверждение после пересылки. Пустой текст — ничего не отправлять.
    "sent": "",
    # Прислали не текст (фото, стикер, голосовое и т.д.).
    "only_text": "⚠️ Принимаются только текстовые сообщения.",
    # Антифлуд. {seconds} — сколько ждать.
    "flood": "⏳ Слишком много сообщений. Подождите {seconds} сек.",
    # Одинаковое сообщение подряд.
    "duplicate": "⚠️ Вы уже отправили это сообщение.",
    # Слишком длинный текст. {max} — лимит символов.
    "too_long": "⚠️ Сообщение слишком длинное (максимум {max} символов).",
    # Пользователь заблокирован. Пустой текст — молча игнорировать.
    "banned": "",
    # Группа не настроена или бот не может в неё писать.
    "unavailable": "⚠️ Сервис временно недоступен, попробуйте позже.",
    # Как пользователь видит ответ из группы. {who} — @username или ID
    # ответившего, {text} — сам ответ.
    "reply": "💬 Ответил {who}:\n\n{text}",
}

# Числовые настройки антифлуда/антиспама (только для пользователей в ЛС).
DEFAULT_SETTINGS: dict[str, int] = {
    "flood_limit": 5,       # не больше N сообщений...
    "flood_window": 10,     # ...за столько секунд
    "flood_mute": 30,       # на сколько секунд глушить нарушителя
    "duplicate_window": 60, # одинаковый текст подряд за N сек — спам (0 = выкл)
    "max_length": 3500,     # максимальная длина сообщения пользователя
}


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
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
        """
    )
    conn.commit()
    if DEFAULT_GROUP_ID and get_group_id() == 0:
        set_group_id(DEFAULT_GROUP_ID)


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


# ---------- users ----------

def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    conn = get_conn()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO users (user_id, username, first_name, first_seen_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, "
        "first_name = excluded.first_name, last_seen_at = excluded.last_seen_at",
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


def count_users() -> tuple[int, int]:
    row = get_conn().execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(is_banned), 0) AS banned FROM users"
    ).fetchone()
    return row["total"], row["banned"]


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
