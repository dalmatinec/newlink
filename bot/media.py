"""Медиа хранятся оригиналами на диске, а file_id — отдельно для каждого бота.
Сменили токен → file_id нового бота ещё нет → файл заливается с диска, file_id запоминается.
Нет ни file_id, ни файла → возвращаем None, вызывающий код покажет экран без медиа."""
import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from .db import Database

log = logging.getLogger(__name__)

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Bot API не отдаёт ботам файлы больше 20 МБ
KINDS = ("photo", "animation", "video")
KIND_NAMES = {"photo": "фото", "animation": "GIF", "video": "видео"}


class MediaError(Exception):
    """Ошибка, текст которой можно показать админу."""


@dataclass(slots=True)
class MediaFile:
    id: int
    path: Path
    kind: str
    size: int


class MediaStore:
    def __init__(self, db: Database, media_dir: Path) -> None:
        self.db = db
        self.dir = media_dir
        self.bot_id = 0
        self.files: dict[int, MediaFile] = {}
        self.file_ids: dict[int, str] = {}
        self.on_missing: Callable[[int], Awaitable[None]] | None = None
        self._reported_missing: set[int] = set()

    async def load(self, bot_id: int) -> None:
        self.bot_id = bot_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.files = {
            r["id"]: MediaFile(r["id"], self.dir / r["path"], r["kind"], r["size"])
            for r in await self.db.fetchall("SELECT id, path, kind, size FROM media")
        }
        self.file_ids = {
            r["media_id"]: r["file_id"]
            for r in await self.db.fetchall("SELECT media_id, file_id FROM media_file_ids WHERE bot_id = ?", (bot_id,))
        }

    def get(self, media_id: int | None) -> MediaFile | None:
        return self.files.get(media_id) if media_id else None

    def pending_warmup(self) -> list[MediaFile]:
        return [m for m in self.files.values() if m.id not in self.file_ids]

    # ---------- отправка ----------
    async def send(
        self, bot: Bot, chat_id: int, media_id: int, caption: str, markup: InlineKeyboardMarkup | None,
        disable_notification: bool = False, protect_content: bool = False,
    ) -> Message | None:
        m = self.files.get(media_id)
        if m is None:
            return None

        async def call(src: str | FSInputFile) -> Message:
            common = dict(caption=caption or None, reply_markup=markup, disable_notification=disable_notification,
                          protect_content=protect_content)
            if m.kind == "photo":
                return await bot.send_photo(chat_id, photo=src, **common)
            if m.kind == "animation":
                return await bot.send_animation(chat_id, animation=src, **common)
            return await bot.send_video(chat_id, video=src, supports_streaming=True, **common)

        return await self._run(m, call)

    async def edit(
        self, bot: Bot, chat_id: int, message_id: int, media_id: int, caption: str,
        markup: InlineKeyboardMarkup | None,
    ) -> Message | None:
        m = self.files.get(media_id)
        if m is None:
            return None

        async def call(src: str | FSInputFile) -> Message:
            cls = {"photo": InputMediaPhoto, "animation": InputMediaAnimation, "video": InputMediaVideo}[m.kind]
            media = cls(media=src, caption=caption or None)
            if m.kind == "video":
                media.supports_streaming = True
            result = await bot.edit_message_media(
                chat_id=chat_id, message_id=message_id, media=media, reply_markup=markup
            )
            assert isinstance(result, Message)
            return result

        return await self._run(m, call)

    async def _run(self, m: MediaFile, call: Callable[[str | FSInputFile], Awaitable[Message]]) -> Message | None:
        file_id = self.file_ids.get(m.id)
        if file_id is not None:
            try:
                msg = await call(file_id)
                return msg
            except TelegramBadRequest as e:
                if not _is_file_error(e):
                    raise
                log.warning("file_id медиа %s не принят (%s), заливаю с диска", m.id, e.message)
                await self._forget(m.id)
        if not m.path.exists():
            await self._report_missing(m.id)
            return None
        msg = await call(FSInputFile(m.path))
        await self._remember(m, msg)
        return msg

    async def _remember(self, m: MediaFile, msg: Message) -> None:
        file_id = _extract_file_id(msg, m.kind)
        if file_id and self.file_ids.get(m.id) != file_id:
            self.file_ids[m.id] = file_id
            await self.db.execute(
                "INSERT OR REPLACE INTO media_file_ids(media_id, bot_id, file_id) VALUES (?, ?, ?)",
                (m.id, self.bot_id, file_id),
            )

    async def _forget(self, media_id: int) -> None:
        self.file_ids.pop(media_id, None)
        await self.db.execute(
            "DELETE FROM media_file_ids WHERE media_id = ? AND bot_id = ?", (media_id, self.bot_id)
        )

    async def _report_missing(self, media_id: int) -> None:
        log.error("Медиа %s отсутствует на диске и не имеет file_id", media_id)
        if media_id not in self._reported_missing and self.on_missing:
            self._reported_missing.add(media_id)
            await self.on_missing(media_id)

    # ---------- приём от админа ----------
    async def save_from_message(self, bot: Bot, message: Message, max_bytes: int) -> int:
        kind, file_id, size, ext, reusable = _detect(message)
        limit = min(max_bytes, TELEGRAM_DOWNLOAD_LIMIT)
        if size and size > limit:
            raise MediaError(f"Файл весит {size / 1048576:.1f} МБ, а можно не больше {limit / 1048576:.0f} МБ.")
        buf = await bot.download(file_id)
        assert buf is not None
        data = buf.getvalue()
        if len(data) > limit:
            raise MediaError(f"Файл весит {len(data) / 1048576:.1f} МБ, а можно не больше {limit / 1048576:.0f} МБ.")
        sha = hashlib.sha256(data).hexdigest()

        existing = await self.db.fetchone("SELECT id FROM media WHERE sha256 = ?", (sha,))
        if existing is not None:
            media_id = existing["id"]
            m = self.files[media_id]
            if not m.path.exists():  # файл потерян — восстанавливаем из присланного
                await asyncio.to_thread(_write_atomic, m.path, data)
                self._reported_missing.discard(media_id)
            return media_id

        name = f"{sha}.{ext}"
        path = self.dir / name
        await asyncio.to_thread(_write_atomic, path, data)
        media_id = await self.db.execute(
            "INSERT INTO media(sha256, path, kind, size, created_at) VALUES (?, ?, ?, ?, ?)",
            (sha, name, kind, len(data), int(time.time())),
        )
        m = MediaFile(media_id, path, kind, len(data))
        self.files[media_id] = m
        if reusable:  # file_id входящего фото/GIF/видео годится и для отправки
            self.file_ids[media_id] = file_id
            await self.db.execute(
                "INSERT OR REPLACE INTO media_file_ids(media_id, bot_id, file_id) VALUES (?, ?, ?)",
                (media_id, self.bot_id, file_id),
            )
        return media_id

    # ---------- обслуживание ----------
    async def warmup(self, bot: Bot, chat_id: int) -> tuple[int, int]:
        """Заливает с диска все медиа, у которых нет file_id для текущего бота."""
        ok = failed = 0
        for m in self.pending_warmup():
            try:
                msg = await self.send(bot, chat_id, m.id, "", None, disable_notification=True)
                if msg is None:
                    failed += 1
                    continue
                ok += 1
                try:
                    await msg.delete()
                except TelegramBadRequest:
                    pass
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except Exception:  # один битый файл не должен останавливать прогрев
                log.exception("Не удалось прогреть медиа %s", m.id)
                failed += 1
            await asyncio.sleep(0.4)
        return ok, failed

    async def set_kind(self, media_id: int, kind: str) -> None:
        """Сменить способ отправки (видео ↔ GIF). Старые file_id другого типа не подойдут — сбрасываем."""
        m = self.files[media_id]
        m.kind = kind
        self.file_ids.pop(media_id, None)
        await self.db.execute("UPDATE media SET kind = ? WHERE id = ?", (kind, media_id))
        await self.db.execute("DELETE FROM media_file_ids WHERE media_id = ?", (media_id,))

    async def collect_garbage(self) -> int:
        """Удаляет медиа, на которые больше ничего не ссылается."""
        rows = await self.db.fetchall(
            """SELECT id FROM media WHERE id NOT IN (
                   SELECT media_id FROM texts WHERE media_id IS NOT NULL
                   UNION SELECT media_id FROM ads WHERE media_id IS NOT NULL)"""
        )
        for r in rows:
            m = self.files.pop(r["id"], None)
            self.file_ids.pop(r["id"], None)
            await self.db.execute("DELETE FROM media WHERE id = ?", (r["id"],))
            if m is not None:
                m.path.unlink(missing_ok=True)
        return len(rows)


def _detect(message: Message) -> tuple[str, str, int, str, bool]:
    """-> (kind, file_id, size, расширение, file_id пригоден для повторной отправки)"""
    if message.photo:
        p = message.photo[-1]
        return "photo", p.file_id, p.file_size or 0, "jpg", True
    if message.animation:
        a = message.animation
        ext = "gif" if a.mime_type == "image/gif" else "mp4"
        return "animation", a.file_id, a.file_size or 0, ext, True
    if message.video:
        v = message.video
        return "video", v.file_id, v.file_size or 0, "mp4", True
    if message.document:
        d = message.document
        mime = (d.mime_type or "").lower()
        if mime == "image/gif":
            return "animation", d.file_id, d.file_size or 0, "gif", False
        if mime in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
            ext = {"image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
            return "photo", d.file_id, d.file_size or 0, ext, False
        if mime == "video/mp4":
            return "video", d.file_id, d.file_size or 0, "mp4", False
        raise MediaError("Этот формат не поддерживается. Можно: JPG, PNG, WEBP, GIF, MP4.")
    raise MediaError("Пришлите фото, GIF или видео (можно файлом).")


def _extract_file_id(msg: Message, kind: str) -> str | None:
    if kind == "photo" and msg.photo:
        return msg.photo[-1].file_id
    if kind == "animation" and msg.animation:
        return msg.animation.file_id
    if kind == "video" and msg.video:
        return msg.video.file_id
    return None


def _is_file_error(e: TelegramBadRequest) -> bool:
    text = e.message.lower()
    return "file" in text or "wrong type" in text or "wrong remote" in text


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
