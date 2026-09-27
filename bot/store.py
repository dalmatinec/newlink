"""Кэш всего, что читают пользователи: настройки, тексты, кнопки, чаты, реклама, спонсоры.
Пользовательские экраны строятся только из памяти. После правки в админке - `reload()` (миллисекунды)."""
import json
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database

STYLES = (None, "primary", "success", "danger")
STYLE_NAMES = {None: "обычная", "primary": "синяя", "success": "зелёная", "danger": "красная"}
PLACEMENTS = {"start": "🏠 После /start", "link": "🔗 После выдачи ссылки"}


def now() -> int:
    return int(time.time())


@dataclass(slots=True)
class Button:
    key: str
    label: str
    icon: str | None
    style: str | None


@dataclass(slots=True)
class Text:
    key: str
    html: str
    media_id: int | None


@dataclass(slots=True)
class Chat:
    id: int
    title: str
    type: str
    username: str | None
    can_invite: bool
    is_present: bool

    @property
    def kind_name(self) -> str:
        return "канал" if self.type == "channel" else "группа"


@dataclass(slots=True)
class Item:
    id: int
    kind: str           # invite | url
    label: str
    icon: str | None
    style: str | None
    chat_id: int | None
    mode: str           # one_time | request
    ttl_minutes: int
    url: str | None
    wide: bool
    skip_sponsors: bool
    is_active: bool
    broken: bool
    position: int


@dataclass(slots=True)
class Ad:
    id: int
    title: str
    html: str
    media_id: int | None
    buttons: list[list[Any]]
    status: str
    placements: set[str]
    max_views: int
    freq_hours: int
    ends_at: int | None
    views: int
    uniques: int
    started_at: int | None


@dataclass(slots=True)
class Sponsor:
    id: int
    chat_id: int
    title: str
    url: str
    link_mode: str
    target: int
    joins: int
    requests: int
    ends_at: int | None
    is_active: bool
    position: int

    @property
    def progress(self) -> int:
        return self.joins + (self.requests if self.link_mode == "request" else 0)


@dataclass
class Store:
    db: Database
    settings: dict[str, Any] = field(default_factory=dict)
    texts: dict[str, Text] = field(default_factory=dict)
    buttons: dict[str, Button] = field(default_factory=dict)
    chats: dict[int, Chat] = field(default_factory=dict)
    items: dict[int, Item] = field(default_factory=dict)
    ads: dict[int, Ad] = field(default_factory=dict)
    sponsors: dict[int, Sponsor] = field(default_factory=dict)
    admins: dict[int, set[str]] = field(default_factory=dict)
    # готовые выборки
    menu: list[Item] = field(default_factory=list)
    active_ads: list[Ad] = field(default_factory=list)
    active_sponsors: list[Sponsor] = field(default_factory=list)
    sponsor_chats: dict[int, list[Sponsor]] = field(default_factory=dict)

    async def reload(self) -> None:
        db = self.db
        self.settings = {r["key"]: json.loads(r["value"]) for r in await db.fetchall("SELECT key, value FROM settings")}
        self.texts = {r["key"]: Text(r["key"], r["html"], r["media_id"]) for r in await db.fetchall("SELECT * FROM texts")}
        self.buttons = {r["key"]: Button(r["key"], r["label"], r["icon"], r["style"])
                        for r in await db.fetchall("SELECT * FROM buttons")}
        self.chats = {
            r["id"]: Chat(r["id"], r["title"], r["type"], r["username"], bool(r["can_invite"]), bool(r["is_present"]))
            for r in await db.fetchall("SELECT * FROM chats")
        }
        self.items = {
            r["id"]: Item(r["id"], r["kind"], r["label"], r["icon"], r["style"], r["chat_id"], r["mode"],
                          r["ttl_minutes"], r["url"], bool(r["wide"]), bool(r["skip_sponsors"]),
                          bool(r["is_active"]), bool(r["broken"]), r["position"])
            for r in await db.fetchall("SELECT * FROM items ORDER BY position, id")
        }
        self.ads = {
            r["id"]: Ad(r["id"], r["title"], r["html"], r["media_id"], json.loads(r["buttons"]), r["status"],
                        set(filter(None, r["placements"].split(","))), r["max_views"], r["freq_hours"],
                        r["ends_at"], r["views"], r["uniques"], r["started_at"])
            for r in await db.fetchall("SELECT * FROM ads WHERE status != 'finished' OR finished_at > ? ORDER BY id",
                                       (now() - 90 * 86400,))
        }
        self.sponsors = {
            r["id"]: Sponsor(r["id"], r["chat_id"], r["title"], r["url"], r["link_mode"], r["target"], r["joins"],
                             r["requests"], r["ends_at"], bool(r["is_active"]), r["position"])
            for r in await db.fetchall("SELECT * FROM sponsors ORDER BY position, id")
        }
        self.admins = {r["user_id"]: set(filter(None, r["perms"].split(",")))
                       for r in await db.fetchall("SELECT user_id, perms FROM admins")}
        self._build()

    def _build(self) -> None:
        self.menu = [i for i in self.items.values() if i.is_active]
        self.active_ads = [a for a in self.ads.values() if a.status == "active"]
        self.active_sponsors = [s for s in self.sponsors.values() if s.is_active and s.url]
        self.sponsor_chats = {}
        for s in self.sponsors.values():
            self.sponsor_chats.setdefault(s.chat_id, []).append(s)

    # ---------- доступ ----------
    def setting(self, key: str, default: Any = 0) -> Any:
        return self.settings.get(key, default)

    def text(self, key: str) -> Text:
        return self.texts.get(key) or Text(key, "", None)

    def button(self, key: str) -> Button:
        return self.buttons.get(key) or Button(key, key, None, None)

    def item_ready(self, item: Item) -> bool:
        """Кнопка может выдавать ссылки прямо сейчас."""
        if item.kind != "invite":
            return bool(item.url)
        chat = self.chats.get(item.chat_id or 0)
        return bool(chat and chat.can_invite and chat.is_present and not item.broken)

    def perms_of(self, user_id: int, owners: frozenset[int]) -> set[str] | None:
        """None - не админ. Владельцы из .env имеют все права."""
        if user_id in owners:
            return {"*"}
        return self.admins.get(user_id)
