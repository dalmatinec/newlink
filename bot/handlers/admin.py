"""Админка: всё управление через inline-кнопки, /admin — вход."""
from contextlib import suppress
from html import escape

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginUser,
    TelegramObject,
)

from bot import texts
from bot.callbacks import Adm
from bot.config import Config
from bot.db import Database, now
from bot.services.invites import DEFAULT_CLEANUP_HOURS, bind_button, revoke_links
from bot.services.menu import send_greeting
from bot.services.notify import is_admin


class IsAdmin(Filter):
    async def __call__(self, event: TelegramObject, db: Database, config: Config) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and await is_admin(db, config, user.id)


class Form(StatesGroup):
    new_button = State()
    rename = State()
    greeting = State()
    add_admin = State()


router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE, IsAdmin())
router.callback_query.filter(IsAdmin())

CLEANUP_PRESETS = [0, 6, 24, 72]
GREETING_KINDS = ("photo", "video", "animation", "document")


# --- helpers ---

def b(text: str, act: str, id: int = 0, val: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=Adm(act=act, id=id, val=val).pack())


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Колбэк — редактируем то же сообщение, обычное сообщение — отвечаем новым."""
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await target.message.answer(text, reply_markup=markup)
        with suppress(TelegramBadRequest):
            await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


# --- экраны ---

def main_screen() -> tuple[str, InlineKeyboardMarkup]:
    return "🛠 <b>Админка</b>\n\nВыбери раздел:", kb(
        [b("🔘 Кнопки", "buttons")],
        [b("👋 Приветствие", "greet"), b("📊 Статистика", "stats")],
        [b("👮 Админы", "admins"), b("⚙️ Настройки", "settings")],
    )


def button_icon(btn) -> str:
    if not btn["enabled"]:
        return "⏸"
    if btn["chat_id"] is None:
        return "🔌"
    return "⚠️" if btn["broken"] else "✅"


async def buttons_screen(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    buttons = await db.list_buttons()
    text = "🔘 <b>Кнопки меню</b>\n\n✅ работает · ⚠️ чат недоступен · 🔌 чат не привязан · ⏸ выключена"
    if not buttons:
        text += "\n\nКнопок пока нет — создай первую."
    rows = [[b(f"{button_icon(x)} {x['title']}", "btn", x["id"])] for x in buttons]
    return text, kb(*rows, [b("➕ Новая кнопка", "new")], [b("⬅️ Назад", "main")])


async def button_screen(db: Database, button_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    btn = await db.get_button(button_id)
    if btn is None:
        return None
    chat = await db.get_chat(btn["chat_id"]) if btn["chat_id"] else None
    if not btn["enabled"]:
        status = "⏸ Выключена (юзеры её не видят)"
    elif btn["chat_id"] is None:
        status = "🔌 Чат не привязан"
    elif btn["broken"]:
        status = "⚠️ Чат недоступен — привяжи новый"
    else:
        status = "✅ Работает"
    issued = await db.scalar("SELECT COUNT(*) FROM invite_links WHERE button_id=?", button_id)
    joined = await db.scalar("SELECT COUNT(*) FROM joins WHERE button_id=?", button_id)
    text = (
        f"🔘 <b>{escape(btn['title'])}</b>\n\n"
        f"Статус: {status}\n"
        f"Чат: {texts.chat_label(chat) if btn['chat_id'] else '—'}\n"
        f"Режим: {texts.MODES[btn['mode']]}\n"
        f"Срок жизни ссылки: {texts.ttl_label(btn['ttl_minutes'])}\n\n"
        f"Выдано ссылок: {issued} · Вступили: {joined}"
    )
    bid = btn["id"]
    return text, kb(
        [b("✏️ Название", "rename", bid), b("🔁 Режим", "mode", bid)],
        [b(f"⏱ Срок: {texts.ttl_label(btn['ttl_minutes'])}", "ttl", bid)],
        [b("🔗 Заменить чат" if btn["chat_id"] else "🔗 Привязать чат", "bind", bid)],
        [b("🗑 Отозвать ссылки", "revoke", bid),
         b("⏸ Выключить" if btn["enabled"] else "▶️ Включить", "toggle", bid)],
        [b("⬆️", "up", bid), b("⬇️", "down", bid), b("❌ Удалить", "del", bid)],
        [b("⬅️ К списку", "buttons")],
    )


async def bind_screen(bot: Bot, btn) -> tuple[str, InlineKeyboardMarkup]:
    me = await bot.me()
    base = f"https://t.me/{me.username}"
    text = (
        f"🔗 <b>Привязка чата к «{escape(btn['title'])}»</b>\n\n"
        "Добавь меня админом в нужный канал или группу — привяжусь к этой кнопке сам. "
        "Нужно право «Приглашать пользователей» (по кнопкам ниже оно уже отмечено).\n\n"
        "Жду 10 минут. Если я уже админ в том чате — выбери его из подключённых."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить в канал", url=f"{base}?startchannel&admin=invite_users")],
        [InlineKeyboardButton(text="➕ Добавить в группу", url=f"{base}?startgroup=bind&admin=invite_users")],
        [b("📋 Выбрать из подключённых", "pick", btn["id"])],
        [b("⬅️ Отмена", "bind_cancel", btn["id"])],
    ])


async def greeting_screen(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    g = await db.get_setting("greeting", texts.DEFAULT_GREETING)
    kind = {"text": "текст", "photo": "фото", "video": "видео", "animation": "GIF", "document": "файл"}[g["kind"]]
    return (
        f"👋 <b>Приветствие</b>\n\nСейчас: {kind}\n\n{texts.PLACEHOLDERS_HELP}",
        kb(
            [b("👁 Предпросмотр", "greet_preview"), b("✏️ Изменить", "greet_edit")],
            [b("↩️ Сбросить на стандартное", "greet_reset")],
            [b("⬅️ Назад", "main")],
        ),
    )


async def stats_screen(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    ts, day = now(), 86400
    q = db.scalar
    total = await q("SELECT COUNT(*) FROM users")
    lines = [
        "📊 <b>Статистика</b>\n",
        f"👥 Пользователей: {total}",
        f"🆕 Новых за 24 ч / 7 дн / 30 дн: "
        f"{await q('SELECT COUNT(*) FROM users WHERE created_at>?', ts - day)} / "
        f"{await q('SELECT COUNT(*) FROM users WHERE created_at>?', ts - 7 * day)} / "
        f"{await q('SELECT COUNT(*) FROM users WHERE created_at>?', ts - 30 * day)}",
        f"🔥 Активных за 24 ч: {await q('SELECT COUNT(*) FROM users WHERE last_seen>?', ts - day)}",
        f"🚫 Заблокировали бота: {await q('SELECT COUNT(*) FROM users WHERE blocked=1')}",
        "",
        f"🔗 Выдано ссылок: {await q('SELECT COUNT(*) FROM invite_links')}",
        f"✅ Вступили по ссылкам: {await q('SELECT COUNT(*) FROM joins')} "
        f"(за 24 ч: {await q('SELECT COUNT(*) FROM joins WHERE at>?', ts - day)})",
    ]
    per_button = await db.fetchall(
        "SELECT b.title, COUNT(j.id) AS n FROM buttons b LEFT JOIN joins j ON j.button_id=b.id "
        "GROUP BY b.id ORDER BY b.position, b.id"
    )
    if per_button:
        lines += ["", "<b>Вступления по кнопкам:</b>"]
        lines += [f"• {escape(r['title'])}: {r['n']}" for r in per_button]
    return "\n".join(lines), kb([b("🔄 Обновить", "stats"), b("⬅️ Назад", "main")])


async def admins_screen(db: Database, config: Config, viewer_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async def name(uid: int) -> str:
        row = await db.fetchone("SELECT first_name, username FROM users WHERE id=?", uid)
        if row is None:
            return str(uid)
        return escape(row["first_name"] or "") + (f" @{row['username']}" if row["username"] else "") + f" ({uid})"

    lines = ["👮 <b>Админы</b>\n"]
    lines += [f"👑 {await name(uid)}" for uid in sorted(config.super_admins)]
    extra = [uid for uid in await db.list_admins() if uid not in config.super_admins]
    lines += [f"• {await name(uid)}" for uid in extra]
    rows = []
    if viewer_id in config.super_admins:
        rows = [[b(f"❌ Убрать {uid}", "admin_del", uid)] for uid in extra]
        rows.append([b("➕ Добавить админа", "admin_add")])
    else:
        lines.append("\nДобавлять и убирать админов могут только 👑.")
    return "\n".join(lines), kb(*rows, [b("⬅️ Назад", "main")])


async def settings_screen(db: Database) -> tuple[str, InlineKeyboardMarkup]:
    hours = await db.get_setting("cleanup_hours", DEFAULT_CLEANUP_HOURS)
    strict = await db.get_setting("strict_requests", True)
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        "🧹 <b>Автоочистка</b> — отзываю ссылки, по которым не вступили за указанное время, "
        "чтобы в чатах не копились сотни ссылок.\n\n"
        "🔒 <b>Заявки только от владельца</b> — по заявке одобряю только того, кому выдал ссылку. "
        "Если ссылку переслали другому — его заявку отклоню."
    )
    return text, kb(
        [b(f"🧹 Автоочистка: {f'{hours} ч' if hours else 'выкл'}", "set_cleanup")],
        [b(f"🔒 Заявки только от владельца: {'вкл' if strict else 'выкл'}", "set_strict")],
        [b("⬅️ Назад", "main")],
    )


# --- вход ---

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show(message, *main_screen())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show(message, *main_screen())


@router.callback_query(Adm.filter())
async def reset_state(cb: CallbackQuery, state: FSMContext) -> None:
    """Любая навигация отменяет незаконченный ввод."""
    await state.clear()
    raise SkipHandler


@router.callback_query(Adm.filter(F.act == "main"))
async def cb_main(cb: CallbackQuery) -> None:
    await show(cb, *main_screen())


# --- кнопки ---

@router.callback_query(Adm.filter(F.act == "buttons"))
async def cb_buttons(cb: CallbackQuery, db: Database) -> None:
    await show(cb, *await buttons_screen(db))


async def open_button_screen(target: Message | CallbackQuery, db: Database, button_id: int) -> None:
    screen = await button_screen(db, button_id)
    if screen is None:
        screen = await buttons_screen(db)
    await show(target, *screen)


@router.callback_query(Adm.filter(F.act == "btn"))
async def cb_button(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    await open_button_screen(cb, db, callback_data.id)


@router.callback_query(Adm.filter(F.act == "new"))
async def cb_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.new_button)
    await show(cb, "✏️ Пришли название новой кнопки (можно с эмодзи):", kb([b("⬅️ Отмена", "buttons")]))


@router.message(Form.new_button, F.text, ~F.text.startswith("/"))
async def got_new_button(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    title = message.text.strip()[:64]
    await state.clear()
    button_id = await db.create_button(title)
    await db.set_pending_bind(message.from_user.id, button_id)
    await show(message, *await bind_screen(bot, await db.get_button(button_id)))


@router.callback_query(Adm.filter(F.act == "rename"))
async def cb_rename(cb: CallbackQuery, callback_data: Adm, state: FSMContext) -> None:
    await state.set_state(Form.rename)
    await state.update_data(button_id=callback_data.id)
    await show(cb, "✏️ Пришли новое название кнопки:", kb([b("⬅️ Отмена", "btn", callback_data.id)]))


@router.message(Form.rename, F.text, ~F.text.startswith("/"))
async def got_rename(message: Message, state: FSMContext, db: Database) -> None:
    button_id = (await state.get_data())["button_id"]
    await state.clear()
    if await db.get_button(button_id):
        await db.update_button(button_id, title=message.text.strip()[:64])
    await open_button_screen(message, db, button_id)


@router.callback_query(Adm.filter(F.act == "mode"))
async def cb_mode(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    btn = await db.get_button(callback_data.id)
    if btn:
        modes = list(texts.MODES)
        await db.update_button(btn["id"], mode=modes[(modes.index(btn["mode"]) + 1) % len(modes)])
    await open_button_screen(cb, db, callback_data.id)


@router.callback_query(Adm.filter(F.act == "ttl"))
async def cb_ttl(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    btn = await db.get_button(callback_data.id)
    if btn:
        presets = texts.TTL_PRESETS
        cur = btn["ttl_minutes"]
        nxt = presets[(presets.index(cur) + 1) % len(presets)] if cur in presets else presets[0]
        await db.update_button(btn["id"], ttl_minutes=nxt)
    await open_button_screen(cb, db, callback_data.id)


@router.callback_query(Adm.filter(F.act == "toggle"))
async def cb_toggle(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    btn = await db.get_button(callback_data.id)
    if btn:
        await db.update_button(btn["id"], enabled=int(not btn["enabled"]))
    await open_button_screen(cb, db, callback_data.id)


@router.callback_query(Adm.filter(F.act.in_({"up", "down"})))
async def cb_move(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    if await db.get_button(callback_data.id):
        await db.move_button(callback_data.id, -1 if callback_data.act == "up" else 1)
    await open_button_screen(cb, db, callback_data.id)


@router.callback_query(Adm.filter(F.act == "del"))
async def cb_delete(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    btn = await db.get_button(callback_data.id)
    if btn is None:
        await show(cb, *await buttons_screen(db))
        return
    await show(
        cb,
        f"❌ Удалить кнопку «{escape(btn['title'])}»?\n\nВсе её неиспользованные ссылки будут отозваны.",
        kb([b("✅ Да, удалить", "del_yes", btn["id"]), b("⬅️ Нет", "btn", btn["id"])]),
    )


@router.callback_query(Adm.filter(F.act == "del_yes"))
async def cb_delete_yes(cb: CallbackQuery, callback_data: Adm, bot: Bot, db: Database) -> None:
    await cb.answer("Удаляю…")
    await revoke_links(bot, db, "button_id=?", callback_data.id)
    await db.delete_button(callback_data.id)
    await show(cb, *await buttons_screen(db))


@router.callback_query(Adm.filter(F.act == "revoke"))
async def cb_revoke(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    n = await db.scalar(
        "SELECT COUNT(*) FROM invite_links WHERE button_id=? AND revoked=0 AND used_at IS NULL",
        callback_data.id,
    )
    if not n:
        await cb.answer("Активных ссылок нет.", show_alert=True)
        return
    await show(
        cb,
        f"🗑 Отозвать {n} неиспользованных ссылок этой кнопки?\n\n"
        "Они сразу перестанут работать. Кто нажмёт кнопку снова — получит новую.",
        kb([b("✅ Отозвать", "revoke_yes", callback_data.id), b("⬅️ Нет", "btn", callback_data.id)]),
    )


@router.callback_query(Adm.filter(F.act == "revoke_yes"))
async def cb_revoke_yes(cb: CallbackQuery, callback_data: Adm, bot: Bot, db: Database) -> None:
    await cb.answer("Отзываю…")
    n = await revoke_links(bot, db, "button_id=?", callback_data.id)
    await cb.message.answer(f"🗑 Отозвано ссылок: {n}")
    await open_button_screen(cb, db, callback_data.id)


# --- привязка чата ---

@router.callback_query(Adm.filter(F.act == "bind"))
async def cb_bind(cb: CallbackQuery, callback_data: Adm, bot: Bot, db: Database) -> None:
    btn = await db.get_button(callback_data.id)
    if btn is None:
        await show(cb, *await buttons_screen(db))
        return
    await db.set_pending_bind(cb.from_user.id, btn["id"])
    await show(cb, *await bind_screen(bot, btn))


@router.callback_query(Adm.filter(F.act == "bind_cancel"))
async def cb_bind_cancel(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    await db.clear_pending_bind(cb.from_user.id)
    await open_button_screen(cb, db, callback_data.id)


@router.callback_query(Adm.filter(F.act == "pick"))
async def cb_pick(cb: CallbackQuery, callback_data: Adm, db: Database) -> None:
    chats = await db.list_invite_chats()
    if not chats:
        await cb.answer("Я пока нигде не админ с правом приглашать. Добавь меня в чат.", show_alert=True)
        return
    rows = [[b(texts.chat_label(c), "bind_to", callback_data.id, str(c["id"]))] for c in chats]
    await show(cb, "📋 Выбери чат для кнопки:", kb(*rows, [b("⬅️ Назад", "bind", callback_data.id)]))


@router.callback_query(Adm.filter(F.act == "bind_to"))
async def cb_bind_to(cb: CallbackQuery, callback_data: Adm, bot: Bot, db: Database) -> None:
    chat = await db.get_chat(int(callback_data.val))
    if chat is None or not chat["can_invite"] or await db.get_button(callback_data.id) is None:
        await cb.answer("Этот чат больше недоступен.", show_alert=True)
        return
    await bind_button(bot, db, callback_data.id, chat["id"])
    await db.clear_pending_bind(cb.from_user.id)
    await cb.answer("✅ Привязано")
    await open_button_screen(cb, db, callback_data.id)


# --- приветствие ---

@router.callback_query(Adm.filter(F.act == "greet"))
async def cb_greet(cb: CallbackQuery, db: Database) -> None:
    await show(cb, *await greeting_screen(db))


@router.callback_query(Adm.filter(F.act == "greet_preview"))
async def cb_greet_preview(cb: CallbackQuery, bot: Bot, db: Database) -> None:
    await cb.answer()
    await send_greeting(bot, db, cb.from_user.id, cb.from_user)


@router.callback_query(Adm.filter(F.act == "greet_edit"))
async def cb_greet_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Form.greeting)
    await show(
        cb,
        "✏️ Пришли новое приветствие одним сообщением: текст или фото/видео/GIF/файл с подписью. "
        "Форматирование и премиум-эмодзи сохранятся.\n\n" + texts.PLACEHOLDERS_HELP,
        kb([b("⬅️ Отмена", "greet")]),
    )


@router.message(Form.greeting, ~F.text.startswith("/"))
async def got_greeting(message: Message, state: FSMContext, bot: Bot, db: Database) -> None:
    kind = next((k for k in GREETING_KINDS if getattr(message, k)), "text" if message.text else None)
    if kind is None:
        await message.answer("Такой тип не подходит. Пришли текст, фото, видео, GIF или файл.")
        return
    file_id = None
    if kind == "photo":
        file_id = message.photo[-1].file_id
    elif kind != "text":
        file_id = getattr(message, kind).file_id
    await state.clear()
    await db.set_setting("greeting", {"kind": kind, "file_id": file_id, "html": message.html_text or ""})
    await message.answer("✅ Приветствие сохранено. Вот как его увидят:")
    await send_greeting(bot, db, message.chat.id, message.from_user)
    await show(message, *await greeting_screen(db))


@router.callback_query(Adm.filter(F.act == "greet_reset"))
async def cb_greet_reset(cb: CallbackQuery, db: Database) -> None:
    await db.delete_setting("greeting")
    await cb.answer("Сброшено")
    await show(cb, *await greeting_screen(db))


# --- статистика ---

@router.callback_query(Adm.filter(F.act == "stats"))
async def cb_stats(cb: CallbackQuery, db: Database) -> None:
    await show(cb, *await stats_screen(db))


# --- админы ---

@router.callback_query(Adm.filter(F.act == "admins"))
async def cb_admins(cb: CallbackQuery, db: Database, config: Config) -> None:
    await show(cb, *await admins_screen(db, config, cb.from_user.id))


@router.callback_query(Adm.filter(F.act.in_({"admin_add", "admin_del"})))
async def cb_admin_edit(
    cb: CallbackQuery, callback_data: Adm, state: FSMContext, db: Database, config: Config
) -> None:
    if cb.from_user.id not in config.super_admins:
        await cb.answer("Только для 👑", show_alert=True)
        return
    if callback_data.act == "admin_del":
        await db.remove_admin(callback_data.id)
        await show(cb, *await admins_screen(db, config, cb.from_user.id))
        return
    await state.set_state(Form.add_admin)
    await show(
        cb,
        "➕ Пришли Telegram ID нового админа или перешли от него любое сообщение.\n"
        "Он должен хотя бы раз запустить бота, чтобы получать уведомления.",
        kb([b("⬅️ Отмена", "admins")]),
    )


@router.message(Form.add_admin, ~F.text.startswith("/"))
async def got_admin(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    uid = None
    if isinstance(message.forward_origin, MessageOriginUser):
        uid = message.forward_origin.sender_user.id
    elif message.text and message.text.strip().isdigit():
        uid = int(message.text.strip())
    if uid is None:
        await message.answer("Не вижу ID. Пришли число или перешли сообщение (у юзера может быть скрыт профиль — тогда только ID).")
        return
    await state.clear()
    await db.add_admin(uid)
    await show(message, *await admins_screen(db, config, message.from_user.id))


# --- настройки ---

@router.callback_query(Adm.filter(F.act == "settings"))
async def cb_settings(cb: CallbackQuery, db: Database) -> None:
    await show(cb, *await settings_screen(db))


@router.callback_query(Adm.filter(F.act == "set_cleanup"))
async def cb_set_cleanup(cb: CallbackQuery, db: Database) -> None:
    cur = await db.get_setting("cleanup_hours", DEFAULT_CLEANUP_HOURS)
    nxt = CLEANUP_PRESETS[(CLEANUP_PRESETS.index(cur) + 1) % len(CLEANUP_PRESETS)] if cur in CLEANUP_PRESETS else 24
    await db.set_setting("cleanup_hours", nxt)
    await show(cb, *await settings_screen(db))


@router.callback_query(Adm.filter(F.act == "set_strict"))
async def cb_set_strict(cb: CallbackQuery, db: Database) -> None:
    await db.set_setting("strict_requests", not await db.get_setting("strict_requests", True))
    await show(cb, *await settings_screen(db))
