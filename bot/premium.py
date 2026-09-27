"""Проверка: показывает ли Telegram премиум-эмодзи от этого бота.

Бот отправляет себе в чат с админом короткое сообщение с премиум-эмодзи в тексте и иконкой на кнопке
и смотрит, что Telegram вернул. Если эмодзи вырезаны, значит, у бота нет права на премиум-эмодзи,
и дело не в настройках бота, а в аккаунте. Результат запоминается на 10 минут.
"""
import time
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from .ui import button, markup

CACHE_SECONDS = 600

HOW_TO_FIX = (
    "Telegram показывает премиум-эмодзи от бота, только если у аккаунта, который создал бота "
    "в @BotFather, есть Telegram Premium, либо у бота куплен юзернейм на Fragment. "
    "Сохранённые эмодзи никуда не пропадут: как только условие выполнится, они сразу появятся."
)


@dataclass(slots=True)
class PremiumResult:
    text_ok: bool
    button_ok: bool
    checked_at: float

    @property
    def ok(self) -> bool:
        return self.text_ok and self.button_ok


_cache: dict[int, PremiumResult] = {}


def custom_emoji_ids(message: Message) -> list[str]:
    return [e.custom_emoji_id for e in (message.entities or []) + (message.caption_entities or [])
            if e.type == "custom_emoji" and e.custom_emoji_id]


async def check(bot: Bot, chat_id: int, emoji_id: str, force: bool = False) -> PremiumResult | None:
    cached = _cache.get(bot.id)
    if cached is not None and not force and time.monotonic() - cached.checked_at < CACHE_SECONDS:
        return cached
    try:
        msg = await bot.send_message(
            chat_id, f'<tg-emoji emoji-id="{emoji_id}">⭐️</tg-emoji> проверка премиум-эмодзи',
            reply_markup=markup([[button("проверка", emoji_id, cb="noop")]]), disable_notification=True)
    except TelegramAPIError:
        return None
    text_ok = any(e.type == "custom_emoji" for e in (msg.entities or []))
    btn = msg.reply_markup.inline_keyboard[0][0] if msg.reply_markup and msg.reply_markup.inline_keyboard else None
    button_ok = bool(btn and btn.icon_custom_emoji_id)
    try:
        await msg.delete()
    except TelegramAPIError:
        pass
    result = _cache[bot.id] = PremiumResult(text_ok, button_ok, time.monotonic())
    return result


def describe(result: PremiumResult | None) -> str:
    if result is None:
        return "Не получилось проверить премиум-эмодзи, попробуй ещё раз."
    if result.ok:
        return "✨ Telegram показывает премиум-эмодзи этого бота: и в тексте, и на кнопках."
    where = []
    if not result.text_ok:
        where.append("в тексте")
    if not result.button_ok:
        where.append("на кнопках")
    return f"⚠️ Telegram убирает премиум-эмодзи {' и '.join(where)} у этого бота.\n{HOW_TO_FIX}"
