import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import database as db

log = logging.getLogger(__name__)

MODE_LABELS = {
    "every_start": "🔁 Каждый /start",
    "once_ever": "1️⃣ Один раз",
    "interval_hours": "⏱ Раз в N часов",
    "times_per_day": "📅 N раз в сутки",
}

# Типы, у которых есть подпись (caption).
_CAPTION_SENDERS = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "document": "send_document",
    "audio": "send_audio",
    "voice": "send_voice",
}


def mode_label(ad: dict) -> str:
    if ad["mode"] == "interval_hours":
        return f"⏱ Раз в {ad['value']} ч"
    if ad["mode"] == "times_per_day":
        return f"📅 {ad['value']} раз в сутки"
    return MODE_LABELS.get(ad["mode"], ad["mode"])


def extract_content(message: Message) -> dict | None:
    """{"type", "file_id", "text"} из сообщения админа (форвард или вручную)."""
    text = message.html_text if (message.text or message.caption) else ""
    if message.text:
        return {"type": "text", "file_id": None, "text": text}
    if message.photo:
        return {"type": "photo", "file_id": message.photo[-1].file_id, "text": text}
    for kind in ("video", "animation", "document", "audio", "voice", "video_note", "sticker"):
        media = getattr(message, kind)
        if media:
            return {"type": kind, "file_id": media.file_id, "text": text}
    return None


def parse_buttons(raw: str) -> list[list[dict]] | None:
    """Каждая строка — ряд, кнопки в ряду через «|», формат «Текст - ссылка»."""
    rows = []
    for line in raw.strip().splitlines():
        row = []
        for part in line.split("|"):
            if " - " not in part:
                return None
            label, url = (x.strip() for x in part.rsplit(" - ", 1))
            if not label or not url.startswith(("http://", "https://", "tg://")):
                return None
            row.append({"text": label, "url": url})
        if row:
            rows.append(row)
    return rows


def build_keyboard(buttons: list[list[dict]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=b["text"], url=b["url"]) for b in row] for row in buttons]
    )


async def send_ad(bot: Bot, chat_id: int, ad: dict) -> None:
    content = ad["content"]
    kb = build_keyboard(ad["buttons"])
    ctype, file_id, text = content["type"], content["file_id"], content["text"] or None

    if ctype == "text":
        await bot.send_message(chat_id, text, reply_markup=kb)
    elif ctype in _CAPTION_SENDERS:
        await getattr(bot, _CAPTION_SENDERS[ctype])(chat_id, file_id, caption=text, reply_markup=kb)
    elif ctype == "video_note":
        await bot.send_video_note(chat_id, file_id, reply_markup=None if text else kb)
        if text:
            await bot.send_message(chat_id, text, reply_markup=kb)
    elif ctype == "sticker":
        await bot.send_sticker(chat_id, file_id, reply_markup=None if text else kb)
        if text:
            await bot.send_message(chat_id, text, reply_markup=kb)


def _is_due(ad: dict, user_id: int, is_start: bool) -> bool:
    mode = ad["mode"]
    if mode == "every_start":
        return is_start

    show = db.get_ad_show(user_id, ad["slot_id"])
    last = show["last_shown_at"]
    if mode == "once_ever":
        return last is None

    hours_passed = (
        (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600 if last else None
    )
    if mode == "interval_hours":
        return hours_passed is None or hours_passed >= max(ad["value"], 1)
    if mode == "times_per_day":
        n = max(ad["value"], 1)
        if show["shown_count"] >= n:
            return False
        return hours_passed is None or hours_passed >= 24 / n
    return False


async def maybe_show_ad(bot: Bot, user_id: int, is_start: bool) -> None:
    """Показывает не больше одной рекламы за раз — первый подходящий слот.
    «Каждый /start» срабатывает только на /start, остальные режимы — на любое
    сообщение пользователя по своему расписанию."""
    if not db.ads_enabled():
        return
    for ad in db.enabled_ads():
        if not ad["content"] or not _is_due(ad, user_id, is_start):
            continue
        try:
            await send_ad(bot, user_id, ad)
        except TelegramAPIError as e:
            log.warning("Реклама слота %s не отправлена: %s", ad["slot_id"], e)
            return
        db.register_ad_show(user_id, ad["slot_id"])
        return
