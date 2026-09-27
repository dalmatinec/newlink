"""Разбор сообщений админа: HTML с премиум-эмодзи, подписи кнопок с иконкой, контакты."""
import re
from html import unescape

from aiogram.types import Message, MessageEntity

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
LABEL_LIMIT = 64


def html_to_plain(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html))


def message_html(message: Message) -> tuple[str, str]:
    """-> (html с <tg-emoji>, чистый текст). Премиум-эмодзи и форматирование сохраняются как есть."""
    plain = message.text or message.caption or ""
    return (message.html_text if plain else ""), plain


def _u16(s: str) -> bytes:
    return s.encode("utf-16-le")


def _cut(text: str, entities: list[MessageEntity], start16: int, end16: int) -> tuple[str, str | None]:
    """Берёт кусок [start16, end16) в UTF-16 и достаёт из него первое премиум-эмодзи как иконку."""
    raw = _u16(text)
    icon = None
    cut_from = cut_to = None
    for e in entities:
        if e.type == "custom_emoji" and start16 <= e.offset < end16:
            icon = e.custom_emoji_id
            cut_from, cut_to = e.offset, e.offset + e.length
            break
    if cut_from is not None:
        part = raw[start16 * 2:cut_from * 2] + raw[cut_to * 2:end16 * 2]
    else:
        part = raw[start16 * 2:end16 * 2]
    return part.decode("utf-16-le"), icon


def parse_label(message: Message) -> tuple[str, str | None]:
    """Подпись кнопки: первое премиум-эмодзи становится иконкой кнопки, остальное — текстом."""
    text = message.text or ""
    label, icon = _cut(text, list(message.entities or []), 0, len(_u16(text)) // 2)
    label = " ".join(label.split())
    return label[:LABEL_LIMIT], icon


def first_custom_emoji(message: Message) -> str | None:
    for e in (message.entities or []) + (message.caption_entities or []):
        if e.type == "custom_emoji":
            return e.custom_emoji_id
    return None


def normalize_url(raw: str) -> str | None:
    raw = raw.strip()
    if raw.startswith("@") and re.fullmatch(r"@[A-Za-z0-9_]{4,32}", raw):
        return f"https://t.me/{raw[1:]}"
    if re.match(r"^(t\.me|telegram\.me)/", raw):
        return f"https://{raw}"
    if re.match(r"^(https?|tg)://\S+$", raw):
        return raw
    return None


def parse_contacts(message: Message) -> tuple[list[tuple[str, str, str | None]], list[str]]:
    """Строки вида «Текст кнопки | ссылка или @username». -> (контакты, ошибки)."""
    text = message.text or ""
    entities = list(message.entities or [])
    contacts: list[tuple[str, str, str | None]] = []
    errors: list[str] = []
    offset16 = 0
    for line in text.split("\n"):
        len16 = len(_u16(line)) // 2
        chunk, icon = _cut(text, entities, offset16, offset16 + len16)
        offset16 += len16 + 1
        if not chunk.strip():
            continue
        if "|" not in chunk:
            errors.append(f"нет разделителя |: {line.strip()[:40]}")
            continue
        label, url = chunk.rsplit("|", 1)
        label = " ".join(label.split())[:LABEL_LIMIT]
        norm = normalize_url(url)
        if not label or norm is None:
            errors.append(f"неверная строка: {line.strip()[:40]}")
            continue
        contacts.append((label, norm, icon))
    return contacts, errors


_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "pre",
                 "tg-spoiler", "tg-emoji", "blockquote", "span"}


def html_error(html: str) -> str | None:
    """Проверяет HTML перевода так, как его примет Telegram: разрешённые теги, всё закрыто."""
    from html.parser import HTMLParser

    stack: list[str] = []
    errors: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in _ALLOWED_TAGS:
                errors.append(f"тег <{tag}> не поддерживается")
            stack.append(tag)

        def handle_endtag(self, tag):
            if not stack or stack[-1] != tag:
                errors.append(f"лишний или перепутанный </{tag}>")
            else:
                stack.pop()

    P(convert_charrefs=True).feed(html)
    if stack:
        errors.append(f"не закрыт <{stack[-1]}>")
    return errors[0] if errors else None
