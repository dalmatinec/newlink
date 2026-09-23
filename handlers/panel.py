"""Инлайн-админка: /admin в личке с ботом.

Навигация кнопками, экраны редактируются на месте. Где нужно ввести
значение (текст, число, id, рекламу) — бот просит прислать его следующим
сообщением, кнопка «Отмена» возвращает назад.
"""
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import database as db
from config import SUPER_ADMIN_IDS
from services import ads
from utils import is_admin

router = Router(name="panel")


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and is_admin(event.from_user.id)


router.message.filter(F.chat.type == ChatType.PRIVATE, IsAdmin())
router.callback_query.filter(IsAdmin())


class Input(StatesGroup):
    """Ожидание значения от админа. Что именно ждём — лежит в data['target']."""

    waiting = State()


# ---------- вспомогательное ----------

def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def _back(to: str = "p:main") -> list[tuple[str, str]]:
    return [("⬅️ Назад", to)]


async def _show(event: Message | CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    """Колбэк — редактируем экран на месте; сообщение — шлём новый экран."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        try:
            await event.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            return
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            # Сообщение с медиа (превью рекламы) текстом не отредактировать.
        await event.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
        return
    await event.answer(text, reply_markup=kb, disable_web_page_preview=True)


async def _ask(call: CallbackQuery, state: FSMContext, target: str, prompt: str, back: str) -> None:
    await state.set_state(Input.waiting)
    await state.update_data(target=target, back=back)
    await _show(call, prompt, _kb([("❌ Отмена", back)]))


# ---------- главное меню ----------

def _main_screen() -> tuple[str, InlineKeyboardMarkup]:
    group_id = db.get_group_id()
    text = (
        "🛠 <b>Админ-панель</b>\n\n"
        f"Группа поддержки: {f'<code>{group_id}</code>' if group_id else '❌ не задана'}\n"
        f"Реклама: {'🟢 включена' if db.ads_enabled() else '🔴 выключена'}"
    )
    kb = _kb(
        [("👥 Группа", "p:group"), ("✏️ Тексты", "p:texts")],
        [("🛡 Антифлуд", "p:flood"), ("📣 Реклама", "p:ads")],
        [("🚫 Баны", "p:bans"), ("📊 Статистика", "p:stats")],
        [("👮 Админы", "p:admins")],
    )
    return text, kb


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show(message, *_main_screen())


@router.callback_query(F.data == "p:main")
async def cb_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(call, *_main_screen())


# ---------- группа ----------

@router.callback_query(F.data == "p:group")
async def cb_group(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    group_id = db.get_group_id()
    if group_id:
        try:
            title = escape((await bot.get_chat(group_id)).title or "")
        except TelegramAPIError:
            title = "⚠️ бот не видит этот чат"
        current = f"<b>{title}</b>\n<code>{group_id}</code>"
    else:
        current = "❌ не задана"
    text = (
        "👥 <b>Группа поддержки</b>\n\n"
        f"Сейчас: {current}\n\n"
        "Сменить можно двумя способами:\n"
        "• добавить бота в группу админом и написать там /setgroup\n"
        "• нажать кнопку ниже и прислать id группы"
    )
    await _show(call, text, _kb([("✏️ Указать id", "p:group:set")], _back()))


@router.callback_query(F.data == "p:group:set")
async def cb_group_set(call: CallbackQuery, state: FSMContext) -> None:
    await _ask(call, state, "group", "Пришлите id группы (например <code>-1001234567890</code>).", "p:group")


# ---------- тексты ----------

@router.callback_query(F.data == "p:texts")
async def cb_texts(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    keys = list(db.DEFAULT_TEXTS)
    rows = [
        [(db.TEXT_LABELS[k][0], f"p:text:{k}") for k in keys[i : i + 2]]
        for i in range(0, len(keys), 2)
    ]
    await _show(call, "✏️ <b>Тексты</b>\n\nВыберите, какой текст изменить.", _kb(*rows, _back()))


@router.callback_query(F.data.startswith("p:text:"))
async def cb_text(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    key = call.data.split(":", 2)[2]
    label, hint = db.TEXT_LABELS[key]
    value = db.get_text(key)
    text = (
        f"{label}\n<i>{escape(hint)}</i>\n\n"
        "<b>Сейчас:</b>\n"
        + (escape(value) if value else "<i>(пусто — не отправляется)</i>")
    )
    await _show(
        call,
        text,
        _kb(
            [("✏️ Изменить", f"p:textedit:{key}")],
            [("🚫 Не отправлять", f"p:textoff:{key}"), ("♻️ По умолчанию", f"p:textreset:{key}")],
            _back("p:texts"),
        ),
    )


@router.callback_query(F.data.startswith("p:textedit:"))
async def cb_text_edit(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 2)[2]
    await _ask(
        call, state, f"text:{key}",
        "Пришлите новый текст. Жирный, курсив, ссылки и premium-эмодзи сохранятся.\n"
        f"<i>{escape(db.TEXT_LABELS[key][1])}</i>",
        f"p:text:{key}",
    )


@router.callback_query(F.data.startswith("p:textoff:"))
async def cb_text_off(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 2)[2]
    db.set_text(key, "")
    await cb_text(call, state)


@router.callback_query(F.data.startswith("p:textreset:"))
async def cb_text_reset(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 2)[2]
    db.reset_text(key)
    await cb_text(call, state)


# ---------- антифлуд ----------

@router.callback_query(F.data == "p:flood")
async def cb_flood(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lines = ["🛡 <b>Антифлуд и антиспам</b>", "Действует только на пользователей, в группе лимитов нет.\n"]
    rows = []
    for key, label in db.SETTING_LABELS.items():
        value = db.get_setting(key)
        lines.append(f"• {label}: <b>{value}</b>")
        rows.append([(f"{label}: {value}", f"p:setting:{key}")])
    await _show(call, "\n".join(lines), _kb(*rows, _back()))


@router.callback_query(F.data.startswith("p:setting:"))
async def cb_setting(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 2)[2]
    await _ask(
        call, state, f"setting:{key}",
        f"{db.SETTING_LABELS[key]}\nСейчас: <b>{db.get_setting(key)}</b>\n\nПришлите новое число.",
        "p:flood",
    )


# ---------- реклама ----------

@router.callback_query(F.data == "p:ads")
async def cb_ads(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    on = db.ads_enabled()
    rows = [[("🔴 Выключить всю рекламу" if on else "🟢 Включить всю рекламу", "p:ads:toggle")]]
    for ad in db.list_ads():
        if ad["content"]:
            status = "🟢" if ad["enabled"] else "⚪️"
            label = f"{status} Слот {ad['slot_id']} — {ads.mode_label(ad)}"
        else:
            label = f"➕ Слот {ad['slot_id']} — пусто"
        rows.append([(label, f"p:ad:{ad['slot_id']}")])
    text = (
        "📣 <b>Реклама</b>\n\n"
        f"Общий выключатель: {'🟢 включена' if on else '🔴 выключена'}\n"
        "За раз пользователь видит не больше одной рекламы — первый подходящий слот."
    )
    await _show(call, text, _kb(*rows, _back()))


@router.callback_query(F.data == "p:ads:toggle")
async def cb_ads_toggle(call: CallbackQuery, state: FSMContext) -> None:
    db.set_ads_enabled(not db.ads_enabled())
    await cb_ads(call, state)


async def _ad_screen(event: Message | CallbackQuery, slot: int) -> None:
    ad = db.get_ad(slot)
    has = bool(ad["content"])
    buttons = sum(len(r) for r in ad["buttons"])
    text = (
        f"📣 <b>Слот {slot}</b>\n\n"
        f"Статус: {'🟢 включён' if ad['enabled'] else '⚪️ выключен'}\n"
        f"Контент: {('✅ ' + ad['content']['type']) if has else '❌ не задан'}\n"
        f"Кнопки: {buttons or 'нет'}\n"
        f"Расписание: {ads.mode_label(ad)}"
    )
    rows = [[("✏️ Контент", f"p:adc:{slot}"), ("🔘 Кнопки", f"p:adb:{slot}")],
            [("🗓 Расписание", f"p:ads_:{slot}")]]
    if has:
        rows.append([("⚪️ Выключить" if ad["enabled"] else "🟢 Включить", f"p:adt:{slot}"),
                     ("👁 Превью", f"p:adp:{slot}")])
        rows.append([("🗑 Очистить слот", f"p:adx:{slot}")])
    rows.append(_back("p:ads"))
    await _show(event, text, _kb(*rows))


def _slot(call: CallbackQuery) -> int:
    return int(call.data.rsplit(":", 1)[1])


@router.callback_query(F.data.startswith("p:ad:"))
async def cb_ad(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _ad_screen(call, _slot(call))


@router.callback_query(F.data.startswith("p:adc:"))
async def cb_ad_content(call: CallbackQuery, state: FSMContext) -> None:
    slot = _slot(call)
    await _ask(
        call, state, f"ad_content:{slot}",
        "Пришлите рекламу: перешлите готовый пост или отправьте текст / фото / видео / GIF / "
        "документ / голосовое / кружок / стикер. Форматирование сохранится.",
        f"p:ad:{slot}",
    )


@router.callback_query(F.data.startswith("p:adb:"))
async def cb_ad_buttons(call: CallbackQuery, state: FSMContext) -> None:
    slot = _slot(call)
    await _ask(
        call, state, f"ad_buttons:{slot}",
        "Пришлите кнопки-ссылки. Каждая строка — ряд, кнопки в ряду через «|»:\n\n"
        "<code>Наш канал - https://t.me/channel\n"
        "Сайт - https://example.com | Бот - https://t.me/bot</code>\n\n"
        "Пришлите «-», чтобы убрать все кнопки.",
        f"p:ad:{slot}",
    )


@router.callback_query(F.data.startswith("p:ads_:"))
async def cb_ad_schedule(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    slot = _slot(call)
    rows = [[(label, f"p:adm:{mode}:{slot}")] for mode, label in ads.MODE_LABELS.items()]
    text = (
        "🗓 <b>Когда показывать</b>\n\n"
        "🔁 Каждый /start — при каждом запуске бота\n"
        "1️⃣ Один раз — один раз за всё время\n"
        "⏱ Раз в N часов — не чаще, при любом сообщении пользователя\n"
        "📅 N раз в сутки — не больше N раз, равномерно"
    )
    await _show(call, text, _kb(*rows, _back(f"p:ad:{slot}")))


@router.callback_query(F.data.startswith("p:adm:"))
async def cb_ad_mode(call: CallbackQuery, state: FSMContext) -> None:
    _, _, mode, slot = call.data.split(":")
    slot = int(slot)
    if mode in ("every_start", "once_ever"):
        db.update_ad(slot, mode=mode)
        await _ad_screen(call, slot)
        return
    prompt = "Раз в сколько часов показывать? Пришлите число." if mode == "interval_hours" \
        else "Сколько раз в сутки показывать? Пришлите число."
    await _ask(call, state, f"ad_mode:{mode}:{slot}", prompt, f"p:ads_:{slot}")


@router.callback_query(F.data.startswith("p:adt:"))
async def cb_ad_toggle(call: CallbackQuery, state: FSMContext) -> None:
    slot = _slot(call)
    db.update_ad(slot, enabled=not db.get_ad(slot)["enabled"])
    await _ad_screen(call, slot)


@router.callback_query(F.data.startswith("p:adp:"))
async def cb_ad_preview(call: CallbackQuery, bot: Bot) -> None:
    slot = _slot(call)
    ad = db.get_ad(slot)
    await call.answer()
    try:
        await ads.send_ad(bot, call.from_user.id, ad)
    except TelegramAPIError as e:
        await call.message.answer(f"❌ Не удалось показать: {escape(e.message)}")
        return
    await _ad_screen(call.message, slot)


@router.callback_query(F.data.startswith("p:adx:"))
async def cb_ad_clear(call: CallbackQuery) -> None:
    slot = _slot(call)
    await _show(call, f"Очистить слот {slot}? Реклама и кнопки удалятся.",
                _kb([("🗑 Да, очистить", f"p:adxx:{slot}"), ("❌ Нет", f"p:ad:{slot}")]))


@router.callback_query(F.data.startswith("p:adxx:"))
async def cb_ad_clear_confirm(call: CallbackQuery) -> None:
    slot = _slot(call)
    db.update_ad(slot, enabled=False, content=None, buttons=[], mode="every_start", value=1)
    db.reset_ad_shows(slot)
    await _ad_screen(call, slot)


# ---------- баны ----------

@router.callback_query(F.data == "p:bans")
async def cb_bans(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    banned = db.banned_users()
    lines = ["🚫 <b>Баны</b>", "В группе можно банить реплаем: /ban, /unban, /info.\n"]
    rows = [[("🚫 Забанить по id", "p:ban:add")]]
    for u in banned:
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["user_id"]))
        rows.append([(f"✅ Разбанить {name}", f"p:unban:{u['user_id']}")])
    lines.append(f"Сейчас в бане: {len(banned)}" if banned else "Никто не забанен.")
    await _show(call, "\n".join(lines), _kb(*rows, _back()))


@router.callback_query(F.data == "p:ban:add")
async def cb_ban_add(call: CallbackQuery, state: FSMContext) -> None:
    await _ask(call, state, "ban", "Пришлите id пользователя (или перешлите его сообщение).", "p:bans")


@router.callback_query(F.data.startswith("p:unban:"))
async def cb_unban(call: CallbackQuery, state: FSMContext) -> None:
    db.set_banned(_slot(call), False)
    await cb_bans(call, state)


# ---------- статистика ----------

@router.callback_query(F.data == "p:stats")
async def cb_stats(call: CallbackQuery) -> None:
    s = db.get_stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{s['total']}</b>\n"
        f"🆕 Новых: сегодня {s['new_today']} · 7 дн {s['new_week']} · 30 дн {s['new_month']}\n"
        f"🔥 Активных: сегодня {s['active_today']} · 7 дн {s['active_week']}\n"
        f"🚫 В бане: {s['banned']}\n\n"
        f"📨 Сообщений от пользователей: сегодня {s['msgs_today'][0]} · 7 дн {s['msgs_week'][0]}\n"
        f"💬 Ответов из группы: сегодня {s['msgs_today'][1]} · 7 дн {s['msgs_week'][1]}"
    )
    await _show(call, text, _kb([("🔄 Обновить", "p:stats")], _back()))


# ---------- админы ----------

@router.callback_query(F.data == "p:admins")
async def cb_admins(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    lines = ["👮 <b>Админы</b>\n"]
    lines += [f"• <code>{i}</code> — главный" for i in sorted(SUPER_ADMIN_IDS)]
    extra = sorted(db.list_admins() - SUPER_ADMIN_IDS)
    lines += [f"• <code>{i}</code>" for i in extra]
    rows = []
    if call.from_user.id in SUPER_ADMIN_IDS:
        rows.append([("➕ Добавить админа", "p:admin:add")])
        rows += [[(f"❌ Удалить {i}", f"p:admin:del:{i}")] for i in extra]
    else:
        lines.append("\nДобавлять и удалять админов может только главный админ.")
    await _show(call, "\n".join(lines), _kb(*rows, _back()))


@router.callback_query(F.data == "p:admin:add")
async def cb_admin_add(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in SUPER_ADMIN_IDS:
        await call.answer("Только для главного админа", show_alert=True)
        return
    await _ask(call, state, "admin", "Пришлите id нового админа (или перешлите его сообщение).", "p:admins")


@router.callback_query(F.data.startswith("p:admin:del:"))
async def cb_admin_del(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in SUPER_ADMIN_IDS:
        await call.answer("Только для главного админа", show_alert=True)
        return
    db.remove_admin(_slot(call))
    await cb_admins(call, state)


# ---------- приём введённых значений ----------

def _user_id_from(message: Message) -> int | None:
    origin = message.forward_origin
    if origin is not None and getattr(origin, "sender_user", None):
        return origin.sender_user.id
    try:
        return int((message.text or "").strip())
    except ValueError:
        return None


def _number(message: Message) -> int | None:
    raw = (message.text or "").strip()
    return int(raw) if raw.isdigit() else None


@router.message(Input.waiting)
async def on_input(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    target: str = data["target"]
    back: str = data["back"]
    retry = _kb([("❌ Отмена", back)])

    async def done(text: str, to: str) -> None:
        await state.clear()
        await message.answer(text, reply_markup=_kb([("⬅️ Назад", to)]))

    if target == "group":
        try:
            chat_id = int((message.text or "").strip())
            chat = await bot.get_chat(chat_id)
        except (ValueError, TelegramAPIError):
            await message.answer("❌ Бот не видит такой чат. Проверьте id и что бот добавлен в группу.", reply_markup=retry)
            return
        db.set_group_id(chat_id)
        await done(f"✅ Группа поддержки: <b>{escape(chat.title or '')}</b> (<code>{chat_id}</code>)", "p:group")

    elif target.startswith("text:"):
        key = target.split(":", 1)[1]
        if not (message.text or message.caption):
            await message.answer("Нужен текст.", reply_markup=retry)
            return
        db.set_text(key, message.html_text)
        await done(f"✅ Текст «{db.TEXT_LABELS[key][0]}» сохранён.", f"p:text:{key}")

    elif target.startswith("setting:"):
        key = target.split(":", 1)[1]
        value = _number(message)
        if value is None:
            await message.answer("Пришлите целое число (0 или больше).", reply_markup=retry)
            return
        db.set_setting(key, value)
        await done(f"✅ {db.SETTING_LABELS[key]}: <b>{value}</b>", "p:flood")

    elif target.startswith("ad_content:"):
        slot = int(target.split(":", 1)[1])
        content = ads.extract_content(message)
        if content is None:
            await message.answer("Такой тип сообщения не поддерживается.", reply_markup=retry)
            return
        # Кнопки из пересланного поста подхватываем, если свои не заданы.
        fields = {"content": content}
        if message.reply_markup and not db.get_ad(slot)["buttons"]:
            fields["buttons"] = [
                [{"text": b.text, "url": b.url} for b in row if b.url]
                for row in message.reply_markup.inline_keyboard
            ]
        db.update_ad(slot, **fields)
        db.reset_ad_shows(slot)
        await state.clear()
        await message.answer("✅ Реклама сохранена. Не забудьте включить слот.")
        await _ad_screen(message, slot)

    elif target.startswith("ad_buttons:"):
        slot = int(target.split(":", 1)[1])
        raw = (message.text or "").strip()
        buttons = [] if raw == "-" else ads.parse_buttons(raw)
        if buttons is None:
            await message.answer(
                "❌ Не понял формат. Нужно: <code>Текст - https://ссылка</code>, "
                "кнопки в ряду через «|».",
                reply_markup=retry,
            )
            return
        db.update_ad(slot, buttons=buttons)
        await state.clear()
        await _ad_screen(message, slot)

    elif target.startswith("ad_mode:"):
        _, mode, slot = target.split(":")
        value = _number(message)
        if not value:
            await message.answer("Пришлите целое число больше 0.", reply_markup=retry)
            return
        db.update_ad(int(slot), mode=mode, value=value)
        await state.clear()
        await _ad_screen(message, int(slot))

    elif target == "ban":
        user_id = _user_id_from(message)
        if user_id is None:
            await message.answer("Не вижу id. Пришлите число или перешлите сообщение пользователя "
                                 "(если он не скрыл пересылку).", reply_markup=retry)
            return
        db.set_banned(user_id, True)
        await done(f"🚫 Забанен: <code>{user_id}</code>", "p:bans")

    elif target == "admin":
        user_id = _user_id_from(message)
        if user_id is None:
            await message.answer("Не вижу id. Пришлите число или перешлите сообщение человека.",
                                 reply_markup=retry)
            return
        db.add_admin(user_id)
        await done(f"✅ Админ добавлен: <code>{user_id}</code>", "p:admins")
