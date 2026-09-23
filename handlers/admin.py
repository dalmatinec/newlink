"""Команды в группе поддержки. Всё остальное — в админ-панели (/admin в ЛС)."""
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

import database as db
from utils import is_admin

router = Router(name="admin")
router.message.filter(F.chat.type != ChatType.PRIVATE)


class IsStaff(BaseFilter):
    """Админ бота или любой участник группы поддержки."""

    async def __call__(self, message: Message) -> bool:
        if message.from_user is None:
            return False
        group_id = db.get_group_id()
        return is_admin(message.from_user.id) or (bool(group_id) and message.chat.id == group_id)


def _target_user(message: Message, command: CommandObject) -> int | None:
    if command.args:
        try:
            return int(command.args.strip())
        except ValueError:
            return None
    if message.reply_to_message:
        return db.find_user_by_message(message.chat.id, message.reply_to_message.message_id)
    return None


@router.message(Command("setgroup"))
async def cmd_setgroup(message: Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    db.set_group_id(message.chat.id)
    await message.answer(
        f"✅ Эта группа теперь группа поддержки.\nID: <code>{message.chat.id}</code>"
    )


@router.message(Command("admin"))
async def cmd_admin_in_group(message: Message) -> None:
    if message.from_user and is_admin(message.from_user.id):
        await message.reply("Админ-панель открывается в личке с ботом: /admin")


@router.message(Command("ban", "unban"), IsStaff())
async def cmd_ban(message: Message, command: CommandObject) -> None:
    user_id = _target_user(message, command)
    if user_id is None:
        await message.reply("Реплаем на сообщение пользователя или с id: /ban 123456")
        return
    banned = command.command == "ban"
    db.set_banned(user_id, banned)
    await message.reply(
        f"{'⛔ Заблокирован' if banned else '✅ Разблокирован'}: <code>{user_id}</code>"
    )


@router.message(Command("info"), IsStaff())
async def cmd_info(message: Message, command: CommandObject) -> None:
    user_id = _target_user(message, command)
    if user_id is None:
        await message.reply("Реплаем на сообщение пользователя или с id: /info 123456")
        return
    user = db.get_user(user_id) or {}
    username = f"@{escape(user['username'])}" if user.get("username") else "—"
    await message.reply(
        f"ID: <code>{user_id}</code>\n"
        f"Имя: {escape(user.get('first_name') or '—')}\n"
        f"Username: {username}\n"
        f"Первый визит: {user.get('first_seen_at') or '—'}\n"
        f"Последний: {user.get('last_seen_at') or '—'}\n"
        f"Бан: {'да' if user.get('is_banned') else 'нет'}\n"
        f'<a href="tg://user?id={user_id}">Открыть профиль</a>'
    )
