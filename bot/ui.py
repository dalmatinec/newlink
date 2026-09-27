"""Клавиатуры и показ экранов: редактируем текущее сообщение, где можно,
иначе отправляем новое и удаляем старое — в личке всегда один экран бота."""
import logging
from html import escape
from typing import Callable, Sequence, TypeVar

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from .app import App, Screen
from .store import Item, Store

log = logging.getLogger(__name__)

T = TypeVar("T")
BLANK = "⠀"  # Telegram не принимает пустой текст

# {имя} и {name} — одно и то же: в админке удобнее писать по-русски
ALIASES = {"name": "имя", "full_name": "полное_имя", "username": "юзернейм", "mention": "упоминание",
           "id": "id", "button": "кнопка", "time": "время"}


def fill(html: str, *, user: User | None = None, **values: object) -> str:
    """Подставляет плейсхолдеры в HTML-текст из базы, экранируя значения."""
    raw: dict[str, str] = {}
    if user is not None:
        name = escape(user.first_name or "")
        raw.update(
            name=name,
            full_name=escape(user.full_name),
            username=f"@{escape(user.username)}" if user.username else name,
            mention=f'<a href="tg://user?id={user.id}">{name or user.id}</a>',
            id=str(user.id),
        )
    raw.update({k: escape(str(v)) for k, v in values.items()})
    for key, value in raw.items():
        html = html.replace("{" + key + "}", value)
        if key in ALIASES:
            html = html.replace("{" + ALIASES[key] + "}", value)
    return html


def button(label: str, icon: str | None = None, style: str | None = None, *, cb: str | None = None,
           url: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=label or BLANK, icon_custom_emoji_id=icon or None, style=style or None, callback_data=cb, url=url
    )


def sys_button(store: Store, key: str, *, cb: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    """Системная кнопка («Назад», «Вступить»…), редактируется в админке."""
    b = store.button(key)
    return button(b.label, b.icon, b.style, cb=cb, url=url)


def item_button(item: Item) -> InlineKeyboardButton:
    if item.kind == "url":
        return button(item.label, item.icon, item.style, url=item.url)
    return button(item.label, item.icon, item.style, cb=f"i:{item.id}")


def menu_rows(store: Store) -> list[list[InlineKeyboardButton]]:
    """Главное меню: кнопки по N в ряд, «широкие» — отдельной строкой."""
    per_row = max(1, min(int(store.setting("per_row", 2)), 4))
    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []
    for item in store.menu:
        if item.wide:
            if current:
                rows.append(current)
                current = []
            rows.append([item_button(item)])
            continue
        current.append(item_button(item))
        if len(current) >= per_row:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return rows


def grid(buttons: Sequence[InlineKeyboardButton], per_row: int) -> list[list[InlineKeyboardButton]]:
    per_row = max(1, min(per_row, 8))
    return [list(buttons[i:i + per_row]) for i in range(0, len(buttons), per_row)]


def paginate(items: Sequence[T], page: int, size: int) -> tuple[Sequence[T], int, int]:
    size = max(1, size)
    pages = max(1, -(-len(items) // size))
    page = min(max(page, 0), pages - 1)
    return items[page * size:(page + 1) * size], page, pages


def nav_row(page: int, pages: int, make_cb: Callable[[int], str]) -> list[InlineKeyboardButton]:
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(button("◀️", cb=make_cb(page - 1)))
    row.append(button(f"{page + 1}/{pages}", cb="noop"))
    if page < pages - 1:
        row.append(button("▶️", cb=make_cb(page + 1)))
    return row


def markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    rows = [r for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def show(
    app: App, user_id: int, chat_id: int, html: str, media_id: int | None,
    kb: InlineKeyboardMarkup | None, *, current: Screen | None = None,
) -> None:
    """Показать экран. `current` — сообщение, которое можно отредактировать (обычно — нажатое)."""
    bot = app.bot
    media = app.media.get(media_id)
    html = html or BLANK
    if current is not None:
        try:
            if media and current.has_media:
                if await app.media.edit(bot, chat_id, current.message_id, media.id, html, kb):
                    app.set_screen(user_id, current.message_id, True)
                    return
            elif not media and not current.has_media:
                await bot.edit_message_text(text=html, chat_id=chat_id, message_id=current.message_id, reply_markup=kb)
                app.set_screen(user_id, current.message_id, False)
                return
        except TelegramBadRequest as e:
            if "not modified" in e.message:
                app.set_screen(user_id, current.message_id, current.has_media)
                return
            log.debug("Редактирование не удалось (%s), отправляю заново", e.message)

    old_id = current.message_id if current is not None else await app.last_screen_id(user_id)
    protect = bool(app.store.setting("protect_content", 0)) and app.perms(user_id) is None
    msg: Message | None = None
    if media:
        msg = await app.media.send(bot, chat_id, media.id, html, kb, protect_content=protect)
    if msg is None:
        msg = await bot.send_message(chat_id, html, reply_markup=kb, protect_content=protect)
    app.set_screen(user_id, msg.message_id, media is not None and msg.content_type != "text")
    if old_id and old_id != msg.message_id:
        await safe_delete(app, chat_id, old_id)


def current_of(message: object) -> Screen | None:
    """Экран из нажатого сообщения (недоступное старше 48 ч не придёт как Message)."""
    if not isinstance(message, Message):
        return None
    return Screen(message.message_id, bool(message.photo or message.animation or message.video or message.document))


async def safe_delete(app: App, chat_id: int, message_id: int) -> None:
    try:
        await app.bot.delete_message(chat_id, message_id)
    except TelegramAPIError:
        pass  # старше 48 часов или уже удалено


def minutes_text(minutes: int) -> str:
    if minutes <= 0:
        return "без срока"
    if minutes % 1440 == 0:
        return f"{minutes // 1440} сут"
    if minutes % 60 == 0:
        return f"{minutes // 60} ч"
    if minutes > 60:
        return f"{minutes // 60} ч {minutes % 60} мин"
    return f"{minutes} мин"
