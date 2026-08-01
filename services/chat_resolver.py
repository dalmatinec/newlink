def parse_chat_source(message) -> str | None:
    """
    Универсальный разбор источника чата/канала: админ может прислать
    ЛЮБОЕ — публичную ссылку, @username, числовой chat_id, либо просто
    переслать сообщение из нужного чата. Бот сам определяет, что прислали.
    """
    forwarded = _from_forward(message)
    if forwarded:
        return forwarded

    raw = (message.text or "").strip()
    if not raw:
        return None

    if "t.me/" in raw:
        username = raw.split("t.me/")[-1].split("?")[0].strip("/")
        if username and not username.startswith("+") and "joinchat" not in raw:
            return f"@{username}"
        return None

    if raw.startswith("@"):
        return raw

    cleaned = raw.replace(" ", "")
    if cleaned.lstrip("-").isdigit():
        return cleaned

    return None


def _from_forward(message) -> str | None:
    origin = getattr(message, "forward_origin", None)
    if origin is None:
        return None
    chat = getattr(origin, "chat", None)
    if chat is None:
        return None
    if chat.username:
        return f"@{chat.username}"
    return str(chat.id)
