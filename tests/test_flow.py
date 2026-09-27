"""Сквозные тесты: бот целиком на фейковом Telegram API.
Запуск: python -m unittest -v   (или python -m pytest -q, если он установлен)"""
import asyncio
import itertools
import tempfile
import unittest
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update

from bot.__main__ import setup
from bot.backup import make_backup, restore_backup
from bot.config import Config
from bot.handlers.admin import router as admin_router
from bot.handlers.admin.core import admin_screens
from bot.handlers.chats import router as chats_router
from bot.handlers.user import router as user_router
from bot.store import now

from .fake_telegram import ChatMessage, FakeTelegram

OWNER, USER, OTHER = 1, 42, 43
CHAN, CHAN2 = -1001, -1002
_ids = itertools.count(1)

ADMIN_RIGHTS = dict(
    can_be_edited=False, is_anonymous=False, can_manage_chat=True, can_delete_messages=False,
    can_manage_video_chats=False, can_restrict_members=False, can_promote_members=False, can_change_info=False,
    can_post_stories=False, can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False,
)


class Harness:
    def __init__(self, tmp: Path) -> None:
        self.tg = FakeTelegram(bot_id=1000)
        self.bot = Bot("1000:TEST", session=self.tg,
                       default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
        self.config = Config(bot_token="x", owner_ids=frozenset({OWNER}), data_dir=tmp, log_level="INFO")

    async def start(self) -> "Harness":
        for router in (chats_router, admin_router, user_router):  # роутеры - модульные синглтоны
            router._parent_router = None
        admin_screens.clear()
        self.app, self.dp, self.guard = await setup(self.config, self.bot)
        self.app.links.create_pause = 0
        await self.app.set_setting("flood_limit", 0)  # тесты жмут быстрее человека
        await self.app.set_setting("ad_gap_minutes", 0)
        return self

    async def stop(self) -> None:
        await self.app.flush()
        await self.app.db.close()

    async def feed(self, **update) -> None:
        await self.dp.feed_update(self.bot, Update.model_validate({"update_id": next(_ids), **update},
                                                                   context={"bot": self.bot}))

    @staticmethod
    def user(uid: int) -> dict:
        return {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"user{uid}"}

    async def send(self, uid: int, text: str | None = None, **extra) -> int:
        mid = next(self.tg._ids)
        self.tg.messages[(uid, mid)] = ChatMessage(mid, uid, text, None, "text", None)
        msg = {"message_id": mid, "date": 0, "chat": {"id": uid, "type": "private"}, "from": self.user(uid), **extra}
        if text is not None:
            msg["text"] = text
            if text.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.feed(message=msg)
        return mid

    def screen_id(self, uid: int) -> int:
        if uid in admin_screens and (uid, admin_screens[uid]) in self.tg.messages:
            return admin_screens[uid]
        return self.app.screens[uid].message_id

    def screen(self, uid: int) -> ChatMessage:
        return self.tg.messages[(uid, self.screen_id(uid))]

    def user_screen(self, uid: int) -> ChatMessage:
        return self.tg.messages[(uid, self.app.screens[uid].message_id)]

    async def click(self, uid: int, data: str, message_id: int | None = None) -> None:
        m = self.tg.messages[(uid, message_id or self.screen_id(uid))]
        await self.feed(callback_query={
            "id": str(next(_ids)), "from": self.user(uid), "chat_instance": "c", "data": data,
            "message": self.tg.msg(self.bot, m).model_dump(by_alias=True, exclude_none=True),
        })

    async def admin(self, data: str) -> ChatMessage:
        if OWNER not in admin_screens:
            await self.send(OWNER, "/admin")
        await self.click(OWNER, data, admin_screens[OWNER])
        return self.tg.messages[(OWNER, admin_screens[OWNER])]

    async def bot_status(self, chat_id: int, status: str = "administrator", by: int = OWNER,
                         can_invite: bool = True, type_: str = "channel", title: str = "Канал") -> None:
        bot_user = {"id": 1000, "is_bot": True, "first_name": "Links"}
        new = {"status": status, "user": bot_user}
        if status == "administrator":
            new.update(ADMIN_RIGHTS, can_invite_users=can_invite)
        if status == "kicked":
            new["until_date"] = 0
        await self.feed(my_chat_member={
            "chat": {"id": chat_id, "type": type_, "title": title}, "from": self.user(by), "date": 0,
            "old_chat_member": {"status": "left", "user": bot_user}, "new_chat_member": new})

    async def joined(self, chat_id: int, uid: int, link: str) -> None:
        await self.feed(chat_member={
            "chat": {"id": chat_id, "type": "channel", "title": "Канал"}, "from": self.user(uid), "date": 0,
            "old_chat_member": {"status": "left", "user": self.user(uid)},
            "new_chat_member": {"status": "member", "user": self.user(uid)},
            "invite_link": _invite(link)})

    async def join_request(self, chat_id: int, uid: int, link: str) -> None:
        await self.feed(chat_join_request={
            "chat": {"id": chat_id, "type": "channel", "title": "Канал"}, "from": self.user(uid),
            "user_chat_id": uid, "date": 0, "invite_link": _invite(link, request=True)})

    async def make_item(self, label: str = "🔥 VIP", chat_id: int = CHAN) -> int:
        await self.admin("x:inew")
        await self.send(OWNER, label)
        await self.bot_status(chat_id)
        return max(self.app.store.items)


def _invite(link: str, request: bool = False) -> dict:
    return {"invite_link": link, "creator": {"id": 1000, "is_bot": True, "first_name": "Links"},
            "creates_join_request": request, "is_primary": False, "is_revoked": False}


def join_url(m: ChatMessage) -> str:
    return m.markup.inline_keyboard[0][0].url


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.h = await Harness(Path(self._tmp.name)).start()
        self.app, self.tg = self.h.app, self.h.tg

    async def asyncTearDown(self) -> None:
        await self.h.stop()
        self._tmp.cleanup()


class LinkFlow(Base):
    async def test_create_bind_issue_reuse_join(self):
        h, app, tg = self.h, self.app, self.tg
        item_id = await h.make_item()
        item = app.store.items[item_id]
        self.assertEqual(item.chat_id, CHAN)
        self.assertTrue(app.store.item_ready(item))
        self.assertIn("теперь ведёт", tg.last(OWNER).text)

        await app.links.refill()  # запас готовых ссылок
        self.assertEqual(app.links.pool_counts[item_id], 5)
        created = tg.links_created

        await h.send(USER, "/start")
        home = h.user_screen(USER)
        self.assertIn("Привет, U42", home.text)
        self.assertEqual(home.markup.inline_keyboard[0][0].callback_data, f"i:{item_id}")

        await h.click(USER, f"i:{item_id}")
        screen = h.user_screen(USER)
        link = join_url(screen)
        self.assertIn("одноразовая", screen.text)
        self.assertEqual(tg.links_created, created, "ссылка должна прийти из запаса, без запроса к Telegram")

        await h.click(USER, "m")
        await h.click(USER, f"i:{item_id}")
        self.assertEqual(join_url(h.user_screen(USER)), link, "повторное нажатие отдаёт ту же ссылку")

        await h.joined(CHAN, USER, link)
        self.assertEqual(await app.db.fetchval("SELECT COUNT(*) FROM joins WHERE item_id = ?", (item_id,)), 1)
        row = await app.db.fetchone("SELECT used_at, revoked FROM invite_links WHERE link = ?", (link,))
        self.assertTrue(row["used_at"] and row["revoked"])
        self.assertFalse(app.links.revoke_queue.empty())

    async def test_deep_link_and_empty_pool(self):
        h, app, tg = self.h, self.app, self.tg
        await app.set_setting("pool_size", 0)
        item_id = await h.make_item()
        await h.send(USER, f"/start i{item_id}")
        self.assertTrue(join_url(h.user_screen(USER)).startswith("https://t.me/+"))
        self.assertEqual(tg.links_created, 1)

    async def test_request_mode_admins_decide(self):
        h, app, tg = self.h, self.app, self.tg
        item_id = await h.make_item()
        await h.admin(f"x:imode:{item_id}")
        self.assertEqual(app.store.items[item_id].mode, "request")
        await h.send(USER, "/start")
        await h.click(USER, f"i:{item_id}")
        screen = h.user_screen(USER)
        self.assertIn("рассмотрят админы", screen.text)
        link = join_url(screen)
        await h.join_request(CHAN, USER, link)
        self.assertEqual((tg.approved, tg.declined), ([], []), "по умолчанию заявки принимают админы")
        # включили автоодобрение: владельца ссылки принимает, чужого отклоняет
        await h.admin("x:settog:auto_approve:g_links")
        await h.join_request(CHAN, OTHER, link)
        self.assertIn((CHAN, OTHER), tg.declined)
        await h.join_request(CHAN, USER, link)
        self.assertIn((CHAN, USER), tg.approved)

    async def test_ttl_and_cleanup(self):
        h, app = self.h, self.app
        item_id = await h.make_item()
        await h.admin(f"x:ittl:{item_id}:15")
        await h.send(USER, "/start")
        await h.click(USER, f"i:{item_id}")
        self.assertIn("действует ещё 15 мин", h.user_screen(USER).text)
        link = join_url(h.user_screen(USER))
        await app.db.execute("UPDATE invite_links SET expires_at = ? WHERE link = ?", (now() - 1, link))
        await app.links.cleanup()
        self.assertEqual(await app.db.fetchval("SELECT revoked FROM invite_links WHERE link = ?", (link,)), 1)
        await h.click(USER, f"i:{item_id}")
        self.assertNotEqual(join_url(h.user_screen(USER)), link, "после отзыва выдаётся новая ссылка")

    async def test_broken_chat_and_rebind(self):
        h, app, tg = self.h, self.app, self.tg
        await app.set_setting("pool_size", 0)
        item_id = await h.make_item()
        tg.dead_chats.add(CHAN)
        await h.send(USER, "/start")
        await h.click(USER, f"i:{item_id}")
        self.assertTrue(app.store.items[item_id].broken)
        self.assertIn("недоступен", tg.last(OWNER).text)
        # замена чата: бот добавлен в новый канал и выбран из подключённых
        await h.bot_status(CHAN2, title="Новый")
        await h.admin(f"x:bindto:{item_id}:{CHAN2}")
        item = app.store.items[item_id]
        self.assertEqual(item.chat_id, CHAN2)
        self.assertFalse(item.broken)
        await h.send(USER, "/start")
        await h.click(USER, f"i:{item_id}")
        self.assertIn(f"L{CHAN2}", join_url(h.user_screen(USER)))

    async def test_bot_kicked_marks_broken(self):
        h, app, tg = self.h, self.app, self.tg
        item_id = await h.make_item()
        await h.bot_status(CHAN, status="kicked", by=99)
        self.assertFalse(app.store.item_ready(app.store.items[item_id]))
        self.assertIn("бота убрали", tg.last(OWNER).text)

    async def test_group_migration(self):
        h, app = self.h, self.app
        item_id = await h.make_item(chat_id=-50)
        await h.feed(message={"message_id": 1, "date": 0, "chat": {"id": -50, "type": "group", "title": "G"},
                              "migrate_to_chat_id": -1009})
        self.assertEqual(app.store.items[item_id].chat_id, -1009)
        self.assertIn(-1009, app.store.chats)


class Ads(Base):
    async def _make_ad(self) -> int:
        await self.h.admin("x:adnew")
        await self.h.send(OWNER, "Лучший канал\nПодписывайся!", reply_markup={
            "inline_keyboard": [[{"text": "Перейти", "url": "https://t.me/best"}]]})
        return max(self.app.store.ads)

    async def test_forwarded_post_buttons_and_start(self):
        h, app, tg = self.h, self.app, self.tg
        ad_id = await self._make_ad()
        ad = app.store.ads[ad_id]
        self.assertEqual(ad.title, "Лучший канал")
        self.assertEqual(ad.buttons[0][:2], ["Перейти", "https://t.me/best"])
        self.assertEqual(ad.status, "draft")
        await h.admin(f"x:adrun:{ad_id}:start")
        self.assertEqual(app.store.ads[ad_id].status, "active")

        await h.send(USER, "/start")
        last = tg.last(USER)
        self.assertIn("Лучший канал", last.text)
        self.assertEqual(last.markup.inline_keyboard[0][0].url, "https://t.me/best")
        self.assertEqual(app.store.ads[ad_id].views, 1)

    async def test_ad_button_color(self):
        ad_id = await self._make_ad()
        more = await self.h.admin(f"x:adcol:{ad_id}:m")
        self.assertIn("Ещё", more.text)
        self.assertEqual(self.app.store.ads[ad_id].buttons[0][3], "primary")

    async def test_frequency_once_and_limit(self):
        h, app, tg = self.h, self.app, self.tg
        ad_id = await self._make_ad()
        await h.admin(f"x:adset1:{ad_id}:freq_hours:-1")
        await h.admin(f"x:adset1:{ad_id}:max_views:2")
        await h.admin(f"x:adrun:{ad_id}:start")
        await h.send(USER, "/start")
        await h.send(USER, "/start")
        self.assertEqual(app.store.ads[ad_id].views, 1, "один раз на человека")
        await h.send(OTHER, "/start")
        await app.flush()
        row = await app.db.fetchone("SELECT status, views, uniques FROM ads WHERE id = ?", (ad_id,))
        self.assertEqual((row["status"], row["views"], row["uniques"]), ("finished", 2, 2))
        self.assertIn("Реклама завершена", tg.last(OWNER).text)
        daily = await app.db.fetchone("SELECT views, uniques FROM ad_daily WHERE ad_id = ?", (ad_id,))
        self.assertEqual((daily["views"], daily["uniques"]), (2, 2))

    async def test_after_link_placement_and_gap(self):
        h, app, tg = self.h, self.app, self.tg
        item_id = await h.make_item()
        ad_id = await self._make_ad()
        await h.admin(f"x:adpl:{ad_id}:start")  # оставляем только после ссылки
        await h.admin(f"x:adrun:{ad_id}:start")
        await app.set_setting("ad_gap_minutes", 60)
        await h.send(USER, "/start")
        self.assertEqual(app.store.ads[ad_id].views, 0)
        await h.click(USER, f"i:{item_id}")
        self.assertIn("Лучший канал", tg.last(USER).text)
        await h.click(USER, "m", h.app.screens[USER].message_id)
        await h.click(USER, f"i:{item_id}")
        self.assertEqual(app.store.ads[ad_id].views, 1, "общая пауза между рекламами")


class Admin(Base):
    async def test_all_screens_open(self):
        h, app = self.h, self.app
        item_id = await h.make_item()
        await h.admin("x:iunew")
        await h.send(OWNER, "Сайт | https://example.com")
        await h.admin("x:adnew")
        await h.send(OWNER, "Реклама")
        ad_id = max(app.store.ads)
        screens = ["a:home", "a:items", "a:chats", f"a:item:{item_id}", f"a:item:{max(app.store.items)}",
                   f"a:bind:{item_id}", f"a:pick:{item_id}", f"a:ittl:{item_id}", f"a:irev:{item_id}",
                   f"a:idel:{item_id}", "a:ads", "a:adset", f"a:ad:{ad_id}", f"a:adlim:{ad_id}",
                   f"a:adfreq:{ad_id}", f"a:adend:{ad_id}", f"a:adrep:{ad_id}", "a:bc", "a:stats", "a:users", f"a:user:{USER}", "a:banned:0", "a:cfg",
                   "a:texts", "a:btns", "a:btn:join", "a:col:btn:join", "a:set", "a:prot", "a:bak", "a:admins",
                   "a:log:0"]
        from bot.handlers.admin.content import GROUPS, TEXTS
        from bot.handlers.admin.system import SETTING_GROUPS
        screens += [f"a:text:{k}" for k in TEXTS] + [f"a:txg:{g}" for g in GROUPS]
        screens += [f"a:setg:{g}" for g in SETTING_GROUPS] + ["a:lay", f"a:lay:{item_id}", f"a:imore:{item_id}", f"a:admore:{ad_id}"]
        for cb in screens:
            m = await h.admin(cb)
            self.assertNotIn("Ошибка", m.text or "", cb)
            self.assertNotIn("Нет доступа", m.text or "", cb)

    async def test_label_with_premium_icon_and_color(self):
        h, app = self.h, self.app
        item_id = await h.make_item()
        await h.admin(f"x:lbl:item:{item_id}")
        await h.send(OWNER, "⭐ Премиум", entities=[{"type": "custom_emoji", "offset": 0, "length": 1,
                                                     "custom_emoji_id": "5368324170671202286"}])
        item = app.store.items[item_id]
        self.assertEqual((item.label, item.icon), ("Премиум", "5368324170671202286"))
        await h.admin(f"x:setsty:item:{item_id}:success")
        self.assertEqual(app.store.items[item_id].style, "success")
        # в админке кнопка показана как у юзеров: с иконкой и цветом, иконка видна в карточке
        listing = await h.admin("a:items")
        btn = listing.markup.inline_keyboard[0][0]
        self.assertEqual((btn.icon_custom_emoji_id, btn.style), ("5368324170671202286", "success"))
        card = await h.admin(f"a:item:{item_id}")
        self.assertIn('emoji-id="5368324170671202286"', card.text)
        # новая кнопка встаёт отдельным рядом, стрелка вверх ставит её в ряд выше
        await h.admin("x:iunew")
        await h.send(OWNER, "Сайт | https://example.com")
        second = max(app.store.items)
        await h.admin(f"x:laymv:{second}:up")
        self.assertEqual([i.id for i in app.store.menu], [item_id, second])
        self.assertEqual(app.store.items[second].row, app.store.items[item_id].row)

    async def test_edit_greeting_placeholders(self):
        h = self.h
        await h.admin("x:htm:text:start")
        await h.send(OWNER, "✨ Йо, {имя}! Твой id {id}",
                     entities=[{"type": "custom_emoji", "offset": 0, "length": 1, "custom_emoji_id": "777"}])
        screen = await h.admin("a:text:start")
        self.assertIn('emoji-id="777"', screen.text, "премиум-эмодзи видно в админке")
        await h.send(USER, "/start")
        self.assertIn("Йо, U42! Твой id 42", h.user_screen(USER).text)

    async def test_admin_permissions(self):
        h, app = self.h, self.app
        await h.admin("x:adnew2")
        await h.send(OWNER, "77")
        self.assertEqual(app.store.admins[77], {"links"})
        await h.send(77, "/admin")
        await h.click(77, "a:ads", admin_screens[77])
        self.assertIn("Нет доступа", h.tg.messages[(77, admin_screens[77])].text)

    async def test_broadcast_with_buttons(self):
        h, app, tg = self.h, self.app, self.tg
        await h.send(USER, "/start")
        await h.send(OTHER, "/start")
        await h.admin("x:bcnew")
        await h.send(OWNER, "Новости!")
        await h.admin("x:bcbtn")
        await h.send(OWNER, "Канал | https://t.me/news")
        await h.admin("x:bcgo")
        await app.broadcaster.task
        cur = app.broadcaster.current
        self.assertEqual(cur.sent, 3)  # владелец тоже пользователь
        copy = tg.last(USER)
        self.assertEqual(copy.markup.inline_keyboard[0][0].url, "https://t.me/news")

    async def test_backup_restore_roundtrip(self):
        h, app = self.h, self.app
        item_id = await h.make_item()
        path = await make_backup(app)
        await app.db.execute("DELETE FROM items")
        await app.reload()
        await restore_backup(app, path)
        self.assertIn(item_id, app.store.items)

    async def test_user_messages_cleaned_and_flood_ban(self):
        h, app, tg = self.h, self.app, self.tg
        mid = await h.send(USER, "привет")
        self.assertNotIn((USER, mid), tg.messages)
        await app.set_setting("flood_limit", 2)
        await app.set_setting("flood_strikes", 1)
        for _ in range(4):
            await h.send(USER, "/start")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertTrue(app.is_banned(USER))


class Layout(Base):
    async def test_rows_like_example_and_user_menu(self):
        """2-2-2, последняя ⬆️ → 2-2-1-1, ещё ⬆️ → 2-3-1; у юзера меню такое же."""
        h, app = self.h, self.app
        ids = []
        for n in range(6):
            await h.admin("x:iunew")
            await h.send(OWNER, f"B{n} | https://example.com/{n}")
            ids.append(max(app.store.items))
        grid = [[ids[0], ids[1]], [ids[2], ids[3]], [ids[4], ids[5]]]
        await app.db.executemany("UPDATE items SET row = ?, position = ? WHERE id = ?",
                                 [(r, p, i) for r, row in enumerate(grid) for p, i in enumerate(row)])
        await app.reload()

        def shape():
            from bot.layout import grid_of
            return [len(r) for r in grid_of(app.store.items.values())]

        await h.admin(f"a:lay:{ids[5]}")
        await h.admin(f"x:laymv:{ids[5]}:up")
        self.assertEqual(shape(), [2, 2, 1, 1])
        m = await h.admin(f"x:laymv:{ids[5]}:up")
        self.assertEqual(shape(), [2, 3, 1])
        self.assertEqual([len(r) for r in m.markup.inline_keyboard[:3]], [2, 3, 1], "админ сразу видит новое меню")
        await h.admin(f"x:laymv:{ids[5]}:left")
        self.assertEqual(app.store.items[ids[5]].position, 1)

        await h.send(USER, "/start")
        menu = h.user_screen(USER).markup.inline_keyboard
        self.assertEqual([len(r) for r in menu], [2, 3, 1])
        self.assertEqual(menu[1][1].url, "https://example.com/5")

    async def test_old_per_row_layout_converted(self):
        from bot.seed import convert_layout
        db = self.app.db
        for n in range(5):
            await db.execute("INSERT INTO items(kind, label, url, wide, position, created_at) VALUES "
                             "('url', ?, 'https://x.y', ?, ?, 0)", (f"B{n}", int(n == 2), n))
        await db.execute("DELETE FROM settings WHERE key = 'layout_rows'")
        await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('per_row', '2')")
        await convert_layout(db)
        await self.app.reload()
        from bot.layout import grid_of
        self.assertEqual([len(r) for r in grid_of(self.app.store.items.values())], [2, 1, 2])


class Premium(Base):
    EMOJI = [{"type": "custom_emoji", "offset": 0, "length": 1, "custom_emoji_id": "555"}]

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        from bot import premium
        premium._cache.clear()

    async def test_warns_when_telegram_strips_premium(self):
        await self.h.admin("x:htm:text:start")
        await self.h.send(OWNER, "✨ Привет", entities=self.EMOJI)
        notice = self.h.screen(OWNER).text
        self.assertIn("Telegram убирает премиум-эмодзи в тексте и на кнопках", notice)
        self.assertIn("Premium", notice)
        stored = await self.app.db.fetchval("SELECT html FROM texts WHERE key = 'start'")
        self.assertIn('emoji-id="555"', stored, "эмодзи сохранены и появятся, когда Telegram разрешит")

    async def test_manual_check_ok(self):
        self.tg.premium_ok = True
        await self.h.admin("x:prem")
        await self.h.send(OWNER, "✨", entities=self.EMOJI)
        self.assertIn("Telegram показывает премиум-эмодзи", self.h.screen(OWNER).text)


class Seed(Base):
    async def test_old_default_texts_upgraded_custom_kept(self):
        from bot.seed import apply_seed, load_seed
        db, seed = self.app.db, load_seed()
        old = seed["texts"]["link_request"].replace("Вступить", "\u00abВступить\u00bb").replace(" - ", " \u2014 ")
        await db.execute("UPDATE texts SET html = ? WHERE key = 'link_request'", (old,))
        await db.execute("UPDATE texts SET html = ? WHERE key = 'start'", ("Мой текст \u2014 свой",))
        await apply_seed(db)
        self.assertEqual(await db.fetchval("SELECT html FROM texts WHERE key = 'link_request'"),
                         seed["texts"]["link_request"])
        self.assertEqual(await db.fetchval("SELECT html FROM texts WHERE key = 'start'"), "Мой текст \u2014 свой")


class LegacyText(Base):
    async def test_old_auto_approve_text_replaced(self):
        from bot.seed import apply_seed, load_seed
        db = self.app.db
        old = "🔗 <b>{кнопка}</b>\n\nНажми \u00abВступить\u00bb и отправь заявку \u2014 она одобрится автоматически."
        await db.execute("UPDATE texts SET html = ? WHERE key = 'link_request'", (old,))
        await apply_seed(db)
        self.assertEqual(await db.fetchval("SELECT html FROM texts WHERE key = 'link_request'"),
                         load_seed()["texts"]["link_request"])


class Performance(Base):
    async def test_indexes_used(self):
        db = self.app.db
        plans = {
            "owner": ("SELECT link FROM invite_links WHERE user_id = 1 AND item_id = 1 AND revoked = 0 "
                      "AND used_at IS NULL AND chat_id = 1 AND mode = 'x'"),
            "pool": ("SELECT link FROM invite_links WHERE item_id = 1 AND chat_id = 1 AND mode = 'x' "
                     "AND user_id IS NULL AND revoked = 0 ORDER BY created_at LIMIT 1"),
            "cleanup": ("SELECT link FROM invite_links WHERE revoked = 0 AND used_at IS NULL "
                        "AND (user_id IS NOT NULL AND assigned_at < 5)"),
            "joins": "SELECT COUNT(*) FROM joins WHERE item_id = 1 AND ts > 5",
            "ad_users": "SELECT ad_id FROM ad_users WHERE user_id = 1 AND ad_id IN (1, 2)",
            "users_seen": "SELECT COUNT(*) FROM users WHERE last_seen > 5",
        }
        for name, sql in plans.items():
            plan = " ".join(r[3] for r in await db.fetchall("EXPLAIN QUERY PLAN " + sql))
            self.assertTrue("INDEX" in plan or "PRIMARY KEY" in plan, f"{name}: {plan}")

    async def test_click_is_fast(self):
        """1000 выдач ссылок из запаса: без сети это должно занимать доли секунды."""
        import time
        h, app = self.h, self.app
        item_id = await h.make_item()
        await app.set_setting("pool_size", 1000)
        await app.links.refill()
        item = app.store.items[item_id]
        t = time.perf_counter()
        for uid in range(10_000, 11_000):
            await app.links.issue(item, uid)
        elapsed = time.perf_counter() - t
        self.assertLess(elapsed, 3.0, f"{elapsed:.2f} c на 1000 выдач")
        print(f"\n  1000 выдач из запаса: {elapsed * 1000:.0f} мс ({elapsed:.4f} с на 1000)")


if __name__ == "__main__":
    unittest.main()
