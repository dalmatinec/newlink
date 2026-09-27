"""События каналов и групп: бота добавили/выкинули/дали права, вступления, заявки, миграция группы."""
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatJoinRequest, ChatMemberAdministrator, ChatMemberUpdated, Message

from ..app import App
from ..store import now
from ..ui import button, markup

router = Router(name="chats")

GROUPS = {"group", "supergroup", "channel"}
GONE = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


def _in_chat(member) -> bool:
    if member.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(member, "is_member", False))
    return member.status not in GONE


async def _tell(app: App, user_id: int, html: str, cb: str | None = None, label: str = "") -> None:
    kb = markup([[button(label, cb=cb)]]) if cb else None
    try:
        await app.bot.send_message(user_id, html, reply_markup=kb)
    except TelegramAPIError:
        pass


@router.my_chat_member(F.chat.type == "private")
async def private_status(event: ChatMemberUpdated, app: App) -> None:
    await app.mark_blocked(event.chat.id, event.new_chat_member.status == ChatMemberStatus.KICKED)


@router.my_chat_member(F.chat.type.in_(GROUPS))
async def bot_status(event: ChatMemberUpdated, app: App) -> None:
    chat, new = event.chat, event.new_chat_member
    present = new.status not in GONE
    can_invite = isinstance(new, ChatMemberAdministrator) and bool(new.can_invite_users)
    title = chat.title or str(chat.id)
    await app.db.execute(
        "INSERT INTO chats(id, title, type, username, can_invite, is_present, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title = excluded.title, type = excluded.type, username = excluded.username, "
        "can_invite = excluded.can_invite, is_present = excluded.is_present, updated_at = excluded.updated_at",
        (chat.id, title, chat.type, chat.username, int(can_invite), int(present), now()),
    )
    await app.reload()

    actor = event.from_user
    is_admin = app.perms(actor.id) is not None
    pending = app.pending_bind(actor.id) if is_admin else None
    t = escape(title)

    if not can_invite:
        reason = "бота убрали из чата" if not present else "у бота нет права Приглашать пользователей"
        await app.links.chat_failed(chat.id, reason)
        if not present and app.store.sponsor_chats.get(chat.id):
            names = ", ".join(f"{escape(s.title)}" for s in app.store.sponsor_chats[chat.id] if s.is_active)
            if names:
                await app.alert(f"⚠️ Бота убрали из канала спонсора {names}: подписку на него проверить нельзя.",
                                perm="sponsors")
        if pending and present and new.status != ChatMemberStatus.MEMBER:
            await _tell(app, actor.id, f"Я в {t}, но без права Приглашать пользователей. Выдай его - и привяжусь.")
        return  # статус member: права обычно приходят следующим событием

    await app.links.chat_restored(chat.id)
    if pending is None:
        if is_admin:
            await _tell(app, actor.id, f"✅ Я админ в {t}.\nПривязать к кнопке: /admin → 🔗 Кнопки → кнопка → "
                                       "🔄 Чат → 📋 Выбрать из подключённых.")
        return

    app.pending_binds.pop(actor.id, None)
    if pending.kind == "item" and pending.ref in app.store.items:
        await app.links.bind(pending.ref, chat.id)
        item = app.store.items[pending.ref]
        await app.log_action(actor.id, "item.bind", f"{item.id}: {title}",
                             f"{escape(actor.first_name or str(actor.id))}: кнопка {escape(item.label)} → {t}")
        await _tell(app, actor.id, f"✅ Кнопка {escape(item.label)} теперь ведёт в {t}.",
                    f"a:item:{item.id}", "🔗 Открыть кнопку")
    elif pending.kind == "sponsor":
        try:
            url = await app.sponsors.create_link(chat.id, "plain")
        except TelegramAPIError as e:
            await _tell(app, actor.id, f"Не получилось создать ссылку в {t}: {escape(str(e))[:200]}")
            return
        pos = await app.db.fetchval("SELECT COALESCE(MAX(position), 0) + 1 FROM sponsors")
        sponsor_id = await app.db.execute(
            "INSERT INTO sponsors(chat_id, title, url, position, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat.id, title, url, pos, now()))
        await app.reload()
        await app.log_action(actor.id, "sponsor.create", title,
                             f"{escape(actor.first_name or str(actor.id))}: добавил спонсора {t}")
        await _tell(app, actor.id, f"✅ Спонсор {t} добавлен и уже работает.",
                    f"a:sp:{sponsor_id}", "🤝 Открыть спонсора")


@router.chat_member()
async def member_changed(event: ChatMemberUpdated, app: App) -> None:
    if event.invite_link is None:
        return
    if _in_chat(event.new_chat_member) and not _in_chat(event.old_chat_member):
        link, user_id = event.invite_link.invite_link, event.new_chat_member.user.id
        if not await app.links.register_join(event.chat.id, link, user_id):
            await app.sponsors.on_join(event.chat.id, link, user_id)


@router.chat_join_request()
async def join_request(req: ChatJoinRequest, app: App) -> None:
    if req.invite_link is None:
        return
    link = req.invite_link.invite_link
    owner = await app.links.owner_of(link)
    if owner is None:
        await app.sponsors.on_request(req.chat.id, link, req.from_user.id)
        return  # не наша ссылка - решают админы чата
    try:
        if app.store.setting("strict_requests", 1) and owner and owner != req.from_user.id:
            await req.decline()  # ссылку переслали другому
        else:
            await req.approve()
    except TelegramAPIError:
        pass


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message, app: App) -> None:
    """Группа стала супергруппой - у неё новый id, переносим привязки."""
    old, new = message.chat.id, message.migrate_to_chat_id
    await app.db.execute(
        "INSERT OR REPLACE INTO chats(id, title, type, username, can_invite, is_present, updated_at) "
        "SELECT ?, title, 'supergroup', username, can_invite, is_present, ? FROM chats WHERE id = ?", (new, now(), old))
    await app.db.execute("DELETE FROM chats WHERE id = ?", (old,))
    await app.db.execute("UPDATE items SET chat_id = ? WHERE chat_id = ?", (new, old))
    await app.db.execute("UPDATE sponsors SET chat_id = ? WHERE chat_id = ?", (new, old))
    await app.reload()
