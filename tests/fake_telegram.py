"""Фейковый Telegram Bot API для тестов: запоминает запросы и состояние чатов."""
import itertools
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery, ApproveChatJoinRequest, CopyMessage, CreateChatInviteLink, DeclineChatJoinRequest, DeleteMessage,
    EditMessageMedia, EditMessageText, ForwardMessage, GetChatMember, GetFile, GetMe, RevokeChatInviteLink,
    SendAnimation, SendDocument, SendMessage, SendPhoto, SendVideo, SetMyCommands, TelegramMethod,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (
    BufferedInputFile, ChatInviteLink, ChatMemberLeft, ChatMemberMember, File, InputFile, Message, User,
)


@dataclass
class ChatMessage:
    id: int
    chat_id: int
    text: str | None
    caption: str | None
    kind: str  # text | photo | animation | video | document
    markup: Any
    file_id: str | None = None


@dataclass
class FakeTelegram(BaseSession):
    bot_id: int = 1000
    calls: list[TelegramMethod] = field(default_factory=list)
    messages: dict[tuple[int, int], ChatMessage] = field(default_factory=dict)
    bad_file_ids: set[str] = field(default_factory=set)
    uploads: int = 0
    download_data: bytes = b""
    documents: list[bytes] = field(default_factory=list)  # содержимое отправленных ботом файлов
    links_created: int = 0
    revoked: list[str] = field(default_factory=list)
    members: set[tuple[int, int]] = field(default_factory=set)   # (chat_id, user_id) — кто где состоит
    dead_chats: set[int] = field(default_factory=set)            # бот в этих чатах без прав
    flood_chats: set[int] = field(default_factory=set)           # тут Telegram просит подождать
    approved: list[tuple[int, int]] = field(default_factory=list)
    declined: list[tuple[int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        BaseSession.__init__(self)
        self._ids = itertools.count(100)
        self._fids = itertools.count(1)

    async def close(self) -> None:
        pass

    async def stream_content(self, url: str, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.download_data

    # ---------- helpers ----------
    def msg(self, bot: Bot, m: ChatMessage) -> Message:
        data: dict[str, Any] = {"message_id": m.id, "date": 0, "chat": {"id": m.chat_id, "type": "private"}}
        if m.kind == "text":
            data["text"] = m.text
        else:
            data["caption"] = m.caption
            fid = m.file_id or "x"
            if m.kind == "photo":
                data["photo"] = [{"file_id": fid, "file_unique_id": fid, "width": 1, "height": 1}]
            elif m.kind == "animation":
                data["animation"] = {"file_id": fid, "file_unique_id": fid, "width": 1, "height": 1, "duration": 1}
            elif m.kind == "video":
                data["video"] = {"file_id": fid, "file_unique_id": fid, "width": 1, "height": 1, "duration": 1}
            else:
                data["document"] = {"file_id": fid, "file_unique_id": fid}
        if m.markup is not None:
            data["reply_markup"] = m.markup.model_dump(exclude_none=True)
        return Message.model_validate(data, context={"bot": bot})

    def chat(self, chat_id: int) -> list[ChatMessage]:
        return sorted((m for (c, _), m in self.messages.items() if c == chat_id), key=lambda m: m.id)

    def last(self, chat_id: int) -> ChatMessage:
        return self.chat(chat_id)[-1]

    def _media_source(self, method: TelegramMethod, src: Any) -> str:
        if isinstance(src, BufferedInputFile):
            self.documents.append(src.data)
        if isinstance(src, InputFile):
            self.uploads += 1
            return f"fid_{next(self._fids)}"
        if src in self.bad_file_ids:
            raise TelegramBadRequest(method, "Bad Request: wrong file identifier/HTTP URL specified")
        return src

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=self.bot_id, is_bot=True, first_name="Links", username=f"links{self.bot_id}_bot")
        if isinstance(method, (SetMyCommands, AnswerCallbackQuery)):
            return True
        if isinstance(method, DeleteMessage):
            key = (method.chat_id, method.message_id)
            if key not in self.messages:
                raise TelegramBadRequest(method, "Bad Request: message to delete not found")
            del self.messages[key]
            return True
        if isinstance(method, SendMessage):
            return self._store(bot, method.chat_id, "text", method.reply_markup, text=method.text)
        if isinstance(method, (SendPhoto, SendAnimation, SendVideo, SendDocument)):
            kind, src = {
                SendPhoto: ("photo", "photo"), SendAnimation: ("animation", "animation"),
                SendVideo: ("video", "video"), SendDocument: ("document", "document"),
            }[type(method)]
            fid = self._media_source(method, getattr(method, src))
            return self._store(bot, method.chat_id, kind, method.reply_markup, caption=method.caption, file_id=fid)
        if isinstance(method, EditMessageText):
            m = self.messages.get((method.chat_id, method.message_id))
            if m is None or m.kind != "text":
                raise TelegramBadRequest(method, "Bad Request: there is no text in the message to edit")
            if m.text == method.text and _same(m.markup, method.reply_markup):
                raise TelegramBadRequest(method, "Bad Request: message is not modified")
            m.text, m.markup = method.text, method.reply_markup
            return self.msg(bot, m)
        if isinstance(method, EditMessageMedia):
            m = self.messages.get((method.chat_id, method.message_id))
            if m is None or m.kind == "text":
                raise TelegramBadRequest(method, "Bad Request: message can't be edited")
            m.file_id = self._media_source(method, method.media.media)
            m.kind, m.caption, m.markup = method.media.type, method.media.caption, method.reply_markup
            return self.msg(bot, m)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id, file_path="f/x")
        if isinstance(method, CopyMessage):
            return self._store(bot, method.chat_id, "text", method.reply_markup, text="copy")
        if isinstance(method, CreateChatInviteLink):
            if method.chat_id in self.flood_chats:
                raise TelegramRetryAfter(method, "Too Many Requests", retry_after=3)
            if method.chat_id in self.dead_chats:
                raise TelegramBadRequest(method, "Bad Request: not enough rights to manage chat invite link")
            self.links_created += 1
            return ChatInviteLink(invite_link=f"https://t.me/+L{method.chat_id}x{self.links_created}",
                                  creator=User(id=self.bot_id, is_bot=True, first_name="Links"),
                                  creates_join_request=bool(method.creates_join_request), is_primary=False,
                                  is_revoked=False, name=method.name, member_limit=method.member_limit,
                                  expire_date=method.expire_date)
        if isinstance(method, RevokeChatInviteLink):
            self.revoked.append(method.invite_link)
            return ChatInviteLink(invite_link=method.invite_link, creator=User(id=self.bot_id, is_bot=True,
                                  first_name="Links"), creates_join_request=False, is_primary=False, is_revoked=True)
        if isinstance(method, GetChatMember):
            if method.chat_id in self.dead_chats:
                raise TelegramForbiddenError(method, "Forbidden: bot is not a member of the channel chat")
            user = User(id=method.user_id, is_bot=False, first_name="U")
            if (method.chat_id, method.user_id) in self.members:
                return ChatMemberMember(user=user)
            return ChatMemberLeft(user=user)
        if isinstance(method, ApproveChatJoinRequest):
            self.approved.append((method.chat_id, method.user_id))
            return True
        if isinstance(method, DeclineChatJoinRequest):
            self.declined.append((method.chat_id, method.user_id))
            return True
        if isinstance(method, ForwardMessage):
            return self._store(bot, method.chat_id, "text", None, text="forward")
        raise NotImplementedError(type(method).__name__)

    def _store(self, bot: Bot, chat_id: int, kind: str, markup: Any, text: str | None = None,
               caption: str | None = None, file_id: str | None = None) -> Message:
        m = ChatMessage(next(self._ids), chat_id, text, caption, kind, markup, file_id)
        self.messages[(chat_id, m.id)] = m
        return self.msg(bot, m)


def _same(a: Any, b: Any) -> bool:
    dump = lambda x: x.model_dump(exclude_none=True) if x is not None else None  # noqa: E731
    return dump(a) == dump(b)
