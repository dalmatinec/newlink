from html import escape

from aiogram.types import User

import database as db
from config import SUPER_ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in SUPER_ADMIN_IDS or user_id in db.list_admins()


def render(key: str, **values) -> str:
    """Подставляет {переменные} в текст. Через replace, а не format —
    чтобы фигурные скобки в тексте админа ничего не ломали."""
    text = db.get_text(key)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text.strip()


def who(user: User) -> str:
    """@username ответившего, если его нет — ID."""
    if user.username:
        return f"@{escape(user.username)}"
    return f"ID <code>{user.id}</code>"
