import re
from html import escape

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.types import Message

import database as db
from config import SUPER_ADMIN_IDS
from utils import is_admin

router = Router(name="admin")

HELP = (
    "<b>Команды админа</b>\n\n"
    "<b>Группа</b>\n"
    "/setgroup — написать в нужной группе, она станет группой поддержки\n"
    "/setgroup <code>-100…</code> — то же самое из ЛС по id\n"
    "/group — текущая группа\n\n"
    "<b>Тексты</b>\n"
    "/texts — все тексты и их ключи\n"
    "/settext <code>ключ</code> <code>текст</code> — изменить текст "
    "(или реплаем на сообщение: /settext <code>ключ</code> — форматирование сохранится)\n"
    "/resettext <code>ключ</code> — вернуть текст по умолчанию\n\n"
    "<b>Антифлуд</b>\n"
    "/settings — текущие лимиты\n"
    "/set <code>ключ</code> <code>число</code> — изменить лимит\n\n"
    "<b>Пользователи</b>\n"
    "/ban, /unban, /info — реплаем на сообщение в группе или с id\n"
    "/stats — статистика\n\n"
    "<b>Админы</b>\n"
    "/admins, /addadmin <code>id</code>, /deladmin <code>id</code>"
)


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and is_admin(message.from_user.id)


class IsStaff(BaseFilter):
    """Админ бота или любой участник группы поддержки."""

    async def __call__(self, message: Message) -> bool:
        if message.from_user is None:
            return False
        group_id = db.get_group_id()
        return is_admin(message.from_user.id) or (bool(group_id) and message.chat.id == group_id)


def _parse_id(raw: str | None) -> int | None:
    try:
        return int((raw or "").strip())
    except ValueError:
        return None


# ---------- справка ----------

@router.message(Command("admin", "help"), IsAdmin())
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


# ---------- группа ----------

@router.message(Command("setgroup"), IsAdmin())
async def cmd_setgroup(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.chat.type != ChatType.PRIVATE and not command.args:
        db.set_group_id(message.chat.id)
        await message.answer(
            f"✅ Эта группа теперь группа поддержки.\nID: <code>{message.chat.id}</code>"
        )
        return

    chat_id = _parse_id(command.args)
    if chat_id is None:
        await message.answer(
            "Напишите /setgroup в самой группе или укажите id: /setgroup <code>-100…</code>"
        )
        return
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramAPIError as e:
        await message.answer(f"❌ Бот не видит этот чат: {escape(e.message)}")
        return
    db.set_group_id(chat_id)
    await message.answer(
        f"✅ Группа поддержки: <b>{escape(chat.title or str(chat_id))}</b> (<code>{chat_id}</code>)"
    )


@router.message(Command("group"), IsAdmin())
async def cmd_group(message: Message, bot: Bot) -> None:
    group_id = db.get_group_id()
    if not group_id:
        await message.answer("Группа не задана. Напишите /setgroup в нужной группе.")
        return
    try:
        title = (await bot.get_chat(group_id)).title
    except TelegramAPIError:
        title = "⚠️ бот не видит чат"
    await message.answer(f"Группа поддержки: <b>{escape(title or '')}</b> (<code>{group_id}</code>)")


# ---------- тексты ----------

@router.message(Command("texts"), IsAdmin())
async def cmd_texts(message: Message) -> None:
    parts = ["<b>Тексты</b> (изменить: /settext ключ текст)\n"]
    for key in db.DEFAULT_TEXTS:
        value = db.get_text(key)
        shown = escape(value) if value else "<i>(пусто — не отправляется)</i>"
        parts.append(f"<code>{key}</code>\n{shown}\n")
    await message.answer("\n".join(parts))


@router.message(Command("settext"), IsAdmin())
async def cmd_settext(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split(maxsplit=1)
    key = args[0] if args else ""
    if key not in db.DEFAULT_TEXTS:
        await message.answer(
            "Укажите ключ: " + ", ".join(f"<code>{k}</code>" for k in db.DEFAULT_TEXTS)
        )
        return

    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        value = message.reply_to_message.html_text
    elif len(args) > 1:
        # html_text сохраняет жирный/курсив/ссылки, набранные прямо в Telegram.
        value = re.sub(r"^/\S+\s+\S+\s?", "", message.html_text, count=1)
    else:
        await message.answer(
            "Нужен текст: /settext ключ текст, или реплай этой командой на сообщение.\n"
            "Чтобы текст не отправлялся — /settext ключ -"
        )
        return

    if value.strip() == "-":
        value = ""
    db.set_text(key, value)
    await message.answer(f"✅ Текст <code>{key}</code> обновлён.")


@router.message(Command("resettext"), IsAdmin())
async def cmd_resettext(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip()
    if key not in db.DEFAULT_TEXTS:
        await message.answer("Укажите ключ из /texts")
        return
    db.reset_text(key)
    await message.answer(f"✅ Текст <code>{key}</code> сброшен.")


# ---------- антифлуд ----------

SETTING_HINTS = {
    "flood_limit": "сообщений за окно (0 — без лимита)",
    "flood_window": "окно антифлуда, сек",
    "flood_mute": "мут за флуд, сек",
    "duplicate_window": "повтор одного текста, сек (0 — выкл)",
    "max_length": "макс. длина сообщения (0 — без лимита)",
}


@router.message(Command("settings"), IsAdmin())
async def cmd_settings(message: Message) -> None:
    lines = ["<b>Антифлуд / антиспам</b> (изменить: /set ключ число)\n"]
    for key in db.DEFAULT_SETTINGS:
        lines.append(f"<code>{key}</code> = {db.get_setting(key)} — {SETTING_HINTS[key]}")
    await message.answer("\n".join(lines))


@router.message(Command("set"), IsAdmin())
async def cmd_set(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if len(args) != 2 or args[0] not in db.DEFAULT_SETTINGS or not args[1].isdigit():
        await message.answer("Формат: /set ключ число. Ключи — в /settings")
        return
    db.set_setting(args[0], int(args[1]))
    await message.answer(f"✅ <code>{args[0]}</code> = {args[1]}")


# ---------- пользователи ----------

def _target_user(message: Message, command: CommandObject) -> int | None:
    if command.args:
        return _parse_id(command.args)
    if message.reply_to_message:
        return db.find_user_by_message(message.chat.id, message.reply_to_message.message_id)
    return None


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


@router.message(Command("stats"), IsAdmin())
async def cmd_stats(message: Message) -> None:
    total, banned = db.count_users()
    await message.answer(f"👥 Пользователей: {total}\n⛔ В бане: {banned}")


# ---------- админы ----------

@router.message(Command("admins"), IsAdmin())
async def cmd_admins(message: Message) -> None:
    lines = [f"<code>{i}</code> (главный)" for i in sorted(SUPER_ADMIN_IDS)]
    lines += [f"<code>{i}</code>" for i in sorted(db.list_admins() - SUPER_ADMIN_IDS)]
    await message.answer("<b>Админы</b>\n" + ("\n".join(lines) or "—"))


@router.message(Command("addadmin", "deladmin"), IsAdmin())
async def cmd_edit_admin(message: Message, command: CommandObject) -> None:
    user_id = _parse_id(command.args)
    if user_id is None and message.reply_to_message and message.reply_to_message.from_user:
        user_id = message.reply_to_message.from_user.id
    if user_id is None:
        await message.answer(f"Формат: /{command.command} id (или реплаем на сообщение человека)")
        return
    if command.command == "addadmin":
        db.add_admin(user_id)
        await message.answer(f"✅ Админ добавлен: <code>{user_id}</code>")
        return
    if user_id in SUPER_ADMIN_IDS:
        await message.answer("Главного админа удалить нельзя (он задан в SUPER_ADMIN_ID).")
        return
    db.remove_admin(user_id)
    await message.answer(f"✅ Админ удалён: <code>{user_id}</code>")

