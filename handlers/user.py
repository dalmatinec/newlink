import logging
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import Message

import database as db
from middlewares.antiflood import AntiFloodMiddleware
from utils import render

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.message.middleware(AntiFloodMiddleware())

log = logging.getLogger(__name__)


async def _forward_to_group(message: Message) -> bool:
    """Пересылает сообщение пользователя в группу и запоминает связку,
    чтобы реплай из группы ушёл именно этому человеку."""
    group_id = db.get_group_id()
    if not group_id:
        return False
    try:
        forwarded = await message.forward(group_id)
    except TelegramAPIError as e:
        log.warning("Не удалось переслать в группу %s: %s", group_id, e)
        return False
    db.map_message(group_id, forwarded.message_id, message.from_user.id)
    return True


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    db.upsert_user(user.id, user.username, user.first_name)
    ok = await _forward_to_group(message)
    if not ok:
        await message.answer(render("unavailable"))
        return
    text = render("start", name=escape(user.first_name or ""))
    if text:
        await message.answer(text)


@router.message(F.text)
async def user_text(message: Message) -> None:
    user = message.from_user
    db.upsert_user(user.id, user.username, user.first_name)
    if not await _forward_to_group(message):
        await message.answer(render("unavailable"))
        return
    text = render("sent")
    if text:
        await message.answer(text)


@router.message()
async def user_not_text(message: Message) -> None:
    await message.answer(render("only_text"))
