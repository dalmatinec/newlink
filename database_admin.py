from datetime import date, datetime, timedelta

from database import get_conn


def init_admin_db() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ad_shows (
            user_id INTEGER,
            slot_id INTEGER,
            shown_date TEXT,
            shown_count INTEGER DEFAULT 0,
            last_shown_at TEXT,
            PRIMARY KEY (user_id, slot_id)
        );

        CREATE TABLE IF NOT EXISTS broadcast_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_count INTEGER DEFAULT 0,
            blocked_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            sent_at TEXT
        );
        """
    )
    conn.commit()


def get_ad_show(user_id: int, slot_id: int) -> dict:
    conn = get_conn()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT * FROM ad_shows WHERE user_id = ? AND slot_id = ?", (user_id, slot_id)
    ).fetchone()
    if not row:
        return {"shown_date": None, "shown_count": 0, "last_shown_at": None}
    data = dict(row)
    # Дневной счётчик сбрасывается на новый день, но last_shown_at храним как есть —
    # он нужен и для режима "once_ever", и для расчёта интервалов.
    if data["shown_date"] != today:
        data["shown_count"] = 0
    return data


def register_ad_show(user_id: int, slot_id: int) -> None:
    conn = get_conn()
    today = date.today().isoformat()
    now = datetime.now().isoformat()
    existing = conn.execute(
        "SELECT shown_date FROM ad_shows WHERE user_id = ? AND slot_id = ?",
        (user_id, slot_id),
    ).fetchone()
    if existing and existing["shown_date"] == today:
        conn.execute(
            "UPDATE ad_shows SET shown_count = shown_count + 1, last_shown_at = ? "
            "WHERE user_id = ? AND slot_id = ?",
            (now, user_id, slot_id),
        )
    else:
        conn.execute(
            "INSERT INTO ad_shows (user_id, slot_id, shown_date, shown_count, last_shown_at) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id, slot_id) DO UPDATE SET "
            "shown_date = excluded.shown_date, shown_count = 1, last_shown_at = excluded.last_shown_at",
            (user_id, slot_id, today, now),
        )
    conn.commit()


def log_broadcast(sent: int, blocked: int, errors: int) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO broadcast_log (sent_count, blocked_count, error_count, sent_at) "
        "VALUES (?, ?, ?, ?)",
        (sent, blocked, errors, datetime.now().isoformat()),
    )
    conn.commit()


def get_stats() -> dict:
    conn = get_conn()
    today = date.today()

    total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    active = conn.execute("SELECT COUNT(*) c FROM users WHERE is_blocked = 0").fetchone()["c"]
    blocked = conn.execute("SELECT COUNT(*) c FROM users WHERE is_blocked = 1").fetchone()["c"]

    def sum_since(days: int) -> tuple[int, int]:
        since = (today - timedelta(days=days)).isoformat()
        row = conn.execute(
            "SELECT COALESCE(SUM(new_users), 0) nu, COALESCE(SUM(starts), 0) st "
            "FROM daily_stats WHERE date >= ?",
            (since,),
        ).fetchone()
        return row["nu"], row["st"]

    new_today, starts_today = sum_since(0)
    new_week, starts_week = sum_since(7)
    new_month, starts_month = sum_since(30)

    return {
        "total_users": total_users,
        "active": active,
        "blocked": blocked,
        "new_today": new_today,
        "new_week": new_week,
        "new_month": new_month,
        "starts_today": starts_today,
        "starts_week": starts_week,
        "starts_month": starts_month,
    }


def all_user_ids() -> list[int]:
    conn = get_conn()
    return [row["user_id"] for row in conn.execute("SELECT user_id FROM users WHERE is_blocked = 0")]
