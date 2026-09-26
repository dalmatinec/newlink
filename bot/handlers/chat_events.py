"""События чатов: бота добавили/выкинули, юзер вступил, заявка, миграция группы."""
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import (
    ChatJoinRequest,
    ChatMemberAdministrator,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.callbacks import Adm
from bot.config import Config
from bot.db import Database
from bot.services.invites import bind_button, mark_chat_broken, register_join
from bot.services.notify import is_admin, notify_admins, safe_send

router = Router()

GROUPS = {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}
GONE = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


@router.my_chat_member(F.chat.type == ChatType.PRIVATE)
async def private_status(event: ChatMemberUpdated, db: Database) -> None:
    await db.set_blocked(event.chat.id, event.new_chat_member.status == ChatMemberStatus.KICKED)


@router.my_chat_member(F.chat.type.in_(GROUPS))
async def bot_status_changed(event: ChatMemberUpdated, bot: Bot, db: Database, config: Config) -> None:
    chat, new = event.chat, event.new_chat_member
    title = escape(chat.title or str(chat.id))
    can_invite = isinstance(new, ChatMemberAdministrator) and bool(new.can_invite_users)
    await db.upsert_chat(chat.id, chat.title, chat.type, can_invite)

    actor = event.from_user
    actor_is_admin = await is_admin(db, config, actor.id)
    pending_button = await db.get_pending_bind(actor.id) if actor_is_admin else None

    if not can_invite:
        broken = await mark_chat_broken(db, chat.id)
        if broken:
            names = ", ".join(f"«{escape(b['title'])}»" for b in broken)
            reason = "Меня убрали из" if new.status in GONE else "У меня нет права «Приглашать» в"
            await notify_admins(
                bot, db, config,
                f"⚠️ {reason} «{title}». Сломались кнопки: {names}.\n\n"
                "Привяжи новый чат: /admin → 🔘 Кнопки → кнопка → 🔗 Привязать чат.",
            )
        elif actor_is_admin and new.status not in GONE and not (
            pending_button and new.status == ChatMemberStatus.MEMBER
        ):
            await safe_send(
                bot, db, actor.id,
                f"Я в «{title}», но без права «Приглашать пользователей». Выдай его — и я смогу выдавать ссылки.",
            )
        return

    # Права есть: если кнопки этого чата были сломаны — оживляем.
    await db.execute("UPDATE buttons SET broken=0 WHERE chat_id=?", chat.id)
    if not actor_is_admin:
        return

    if pending_button and await db.get_button(pending_button):
        await bind_button(bot, db, pending_button, chat.id)
        await db.clear_pending_bind(actor.id)
        button = await db.get_button(pending_button)
        await safe_send(
            bot, db, actor.id,
            f"✅ Кнопка «{escape(button['title'])}» теперь ведёт в «{title}».",
            InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔘 Открыть кнопку", callback_data=Adm(act="btn", id=button["id"]).pack())
            ]]),
        )
    else:
        await safe_send(
            bot, db, actor.id,
            f"✅ Я админ в «{title}».\n\nЧтобы привязать к кнопке: /admin → 🔘 Кнопки → кнопка → "
            "🔗 Привязать чат → 📋 Выбрать из подключённых.",
        )


@router.chat_member()
async def member_changed(event: ChatMemberUpdated, bot: Bot, db: Database) -> None:
    if event.invite_link is None:
        return
    old, new = event.old_chat_member, event.new_chat_member
    was_in = old.status not in GONE and not (
        old.status == ChatMemberStatus.RESTRICTED and not getattr(old, "is_member", True)
    )
    now_in = new.status not in GONE and not (
        new.status == ChatMemberStatus.RESTRICTED and not getattr(new, "is_member", True)
    )
    if now_in and not was_in:
        await register_join(bot, db, event.invite_link.invite_link, new.user.id, event.chat.id)


@router.chat_join_request()
async def join_request(req: ChatJoinRequest, db: Database) -> None:
    if req.invite_link is None:
        return
    row = await db.fetchone("SELECT * FROM invite_links WHERE link=?", req.invite_link.invite_link)
    if row is None:
        return  # чужая ссылка — пусть решают админы чата
    strict = await db.get_setting("strict_requests", True)
    if strict and row["user_id"] != req.from_user.id:
        await req.decline()
        return
    await req.approve()


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message, db: Database) -> None:
    """Группа стала супергруппой — у неё новый id, переносим привязки."""
    old_id, new_id = message.chat.id, message.migrate_to_chat_id
    chat = await db.get_chat(old_id)
    await db.upsert_chat(
        new_id, message.chat.title, ChatType.SUPERGROUP, bool(chat and chat["can_invite"])
    )
    await db.execute("DELETE FROM chats WHERE id=?", old_id)
    await db.execute("UPDATE buttons SET chat_id=? WHERE chat_id=?", new_id, old_id)
