from html import escape

from aiogram.types import User

DEFAULT_GREETING = {
    "kind": "text",
    "file_id": None,
    "html": "👋 Привет, {name}!\n\nВыбери, куда хочешь попасть 👇",
}

PLACEHOLDERS_HELP = (
    "<b>Плейсхолдеры:</b>\n"
    "<code>{name}</code> — имя\n"
    "<code>{full_name}</code> — имя и фамилия\n"
    "<code>{username}</code> — @юзернейм (если нет — имя)\n"
    "<code>{mention}</code> — имя со ссылкой на профиль\n"
    "<code>{id}</code> — Telegram ID"
)

MODES = {
    "one_time": "🔂 Одноразовая (на 1 человека)",
    "request": "📨 По заявке (одобряю автоматически)",
}

TTL_PRESETS = [0, 15, 60, 360, 1440]

EMPTY_MENU = "Пока здесь пусто — загляни позже."
UNAVAILABLE = "⏳ Этот чат временно недоступен, загляни чуть позже."
TRY_LATER = "Слишком много запросов, попробуй через минуту."
ALREADY_MEMBER = "✅ Ты уже состоишь в «{title}»."


def render(template: str, user: User) -> str:
    """Подставляет плейсхолдеры в HTML-шаблон. Значения экранируются."""
    name = escape(user.first_name or "")
    values = {
        "{name}": name,
        "{full_name}": escape(user.full_name),
        "{username}": f"@{user.username}" if user.username else name,
        "{mention}": f'<a href="tg://user?id={user.id}">{name}</a>',
        "{id}": str(user.id),
    }
    for key, value in values.items():
        template = template.replace(key, value)
    return template


def ttl_label(minutes: int) -> str:
    if not minutes:
        return "без срока"
    if minutes % 1440 == 0:
        return f"{minutes // 1440} сут"
    if minutes % 60 == 0:
        return f"{minutes // 60} ч"
    return f"{minutes} мин"


def chat_label(chat) -> str:
    if chat is None:
        return "—"
    kind = "канал" if chat["type"] == "channel" else "группа"
    return f"{escape(chat['title'] or str(chat['id']))} ({kind})"


def link_message(button, expires_at: int | None, now_ts: int) -> str:
    lines = [f"🔗 Ссылка в «{escape(button['title'])}» готова!"]
    if button["mode"] == "request":
        lines.append("Нажми «Вступить» и отправь заявку — она одобрится автоматически.")
    else:
        lines.append("Ссылка одноразовая — сработает только для тебя.")
    if expires_at:
        left = max(1, (expires_at - now_ts + 59) // 60)
        lines.append(f"⏳ Действует ещё {ttl_label(left)}.")
    return "\n".join(lines)
