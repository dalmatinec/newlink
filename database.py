import sqlite3
from datetime import date, datetime

from config import DB_PATH

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """Одно глобальное соединение на процесс — переиспользуем, не открываем
    новое на каждый запрос (важно для минимального расхода ресурсов)."""
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
            first_seen_at TEXT,
            last_seen_at TEXT,
            last_bot_message_id INTEGER,
            is_blocked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            new_users INTEGER DEFAULT 0,
            starts INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()


def get_user(user_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def upsert_user(user_id: int, username: str | None) -> bool:
    """Возвращает True, если пользователь новый (для инкремента статистики)."""
    conn = get_conn()
    now = datetime.now().isoformat()
    existing = get_user(user_id)
    if existing:
        conn.execute(
            "UPDATE users SET username = ?, last_seen_at = ?, is_blocked = 0 WHERE user_id = ?",
            (username, now, user_id),
        )
        conn.commit()
        return False

    conn.execute(
        "INSERT INTO users (user_id, username, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (user_id, username, now, now),
    )
    conn.commit()
    bump_stat("new_users")
    return True


def set_last_message_id(user_id: int, message_id: int | None) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE users SET last_bot_message_id = ? WHERE user_id = ?", (message_id, user_id)
    )
    conn.commit()


def set_blocked(user_id: int, blocked: bool = True) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE users SET is_blocked = ? WHERE user_id = ?", (int(blocked), user_id)
    )
    conn.commit()


def bump_stat(field: str, amount: int = 1) -> None:
    """field: 'new_users' | 'starts'"""
    conn = get_conn()
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO daily_stats (date) VALUES (?) ON CONFLICT(date) DO NOTHING", (today,)
    )
    conn.execute(
        f"UPDATE daily_stats SET {field} = {field} + ? WHERE date = ?", (amount, today)
    )
    conn.commit()
