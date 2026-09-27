"""Пользовательская часть. Всё строится из кэша в памяти; база - только при выдаче ссылки.

Callback-данные:
  m         главное меню
  i:<id>    нажали кнопку-ссылку
  c:<id>    Я подписался на экране спонсоров
"""
import math

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from ..app import App, Screen
from ..richtext import html_to_plain
from ..services.links import LinkBusy, LinkUnavailable
from ..store import Item, now
from ..ui import button, current_of, fill, markup, menu_rows, minutes_text, safe_delete, show, sys_button

router = Router(name="user")
router.message.filter(F.chat.type == "private")


def home(app: App, user: User) -> tuple[str, int | None, object]:
    store = app.store
    text = store.text("start")
    html = fill(text.html, user=user)
    rows = menu_rows(store)
    if not rows:
        html = f"{html}\n\n{store.text('empty_menu').html}".strip()
    return html, text.media_id, markup(rows)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, app: App) -> None:
    await state.set_state(None)
    user, chat_id = message.from_user, message.chat.id
    if app.store.setting("clean_chat", 1):
        await safe_delete(app, chat_id, message.message_id)
    payload = command.args or ""
    if payload.startswith("i") and payload[1:].isdigit():  # прямая ссылка на кнопку: t.me/bot?start=i5
        item = app.store.items.get(int(payload[1:]))
        if item is not None and item.is_active and item.kind == "invite":
            await open_item(app, user, chat_id, item, None, None)
            return
    html, media_id, kb = home(app, user)
    await show(app, user.id, chat_id, html, media_id, kb)
    await app.ads.show(chat_id, user.id, "start")


@router.callback_query(F.data == "m")
async def cb_home(call: CallbackQuery, app: App) -> None:
    await call.answer()
    html, media_id, kb = home(app, call.from_user)
    await show(app, call.from_user.id, call.message.chat.id, html, media_id, kb, current=current_of(call.message))


@router.callback_query(F.data.regexp(r"^[ic]:\d+$"))
async def cb_item(call: CallbackQuery, app: App) -> None:
    kind, item_id = call.data.split(":")
    item = app.store.items.get(int(item_id))
    await open_item(app, call.from_user, call.message.chat.id, item, current_of(call.message), call,
                    recheck=kind == "c")


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


async def open_item(app: App, user: User, chat_id: int, item: Item | None, current: Screen | None,
                    call: CallbackQuery | None, recheck: bool = False) -> None:
    store = app.store
    answered = False

    async def answer(key: str | None = None) -> None:
        nonlocal answered
        if call is not None and not answered:
            answered = True
            text = html_to_plain(store.text(key).html)[:190] if key else None
            await call.answer(text, show_alert=bool(key))

    async def fail(key: str) -> None:
        if call is not None:
            await answer(key)
        else:  # пришли по прямой ссылке - показываем текст экраном
            await show(app, user.id, chat_id, store.text(key).html, None,
                       markup([[sys_button(store, "back", cb="m")]]))

    if item is None or not item.is_active or item.kind != "invite":
        return await fail("unavailable")
    if not store.item_ready(item):
        return await fail("unavailable")

    if not item.skip_sponsors:
        missing = await app.sponsors.missing(user.id)
        if missing:
            if recheck:
                await answer("sponsors_missing")
            await answer()
            sb = store.button("sponsor")
            rows = [[button(s.title or sb.label, sb.icon, sb.style, url=s.url)] for s in missing]
            rows.append([sys_button(store, "check_subs", cb=f"c:{item.id}")])
            rows.append([sys_button(store, "back", cb="m")])
            text = store.text("sponsors")
            await show(app, user.id, chat_id, fill(text.html, user=user, button=item.label), text.media_id,
                       markup(rows), current=current)
            return

    try:
        issued = await app.links.issue(item, user.id)
    except LinkBusy:
        return await fail("busy")
    except LinkUnavailable as e:
        await fail("unavailable")
        await app.links.chat_failed(item.chat_id or 0, str(e))
        return
    await answer()

    text = store.text("link_request" if item.mode == "request" else "link_one_time")
    html = fill(text.html, user=user, button=item.label)
    if issued.expires_at:
        left = max(1, math.ceil((issued.expires_at - now()) / 60))
        html += "\n\n" + fill(store.text("link_ttl").html, time=minutes_text(left))
    kb = markup([[sys_button(store, "join", url=issued.link)], [sys_button(store, "back", cb="m")]])
    await show(app, user.id, chat_id, html, text.media_id, kb, current=current)
    await app.ads.show(chat_id, user.id, "link")


@router.message()
async def any_message(message: Message, app: App) -> None:
    """Случайные сообщения не засоряют чат."""
    if app.store.setting("clean_chat", 1):
        await safe_delete(app, message.chat.id, message.message_id)
