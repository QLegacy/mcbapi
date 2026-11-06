from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

if TYPE_CHECKING:  # pragma: no cover
    from .client import Bot


# ----------------------------------------------------------------- outgoing --

@dataclass
class Button:
    """Inline button: either ``data`` (comes back as a Callback event) or ``url``."""
    text: str
    data: Optional[str] = None
    url: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        if (self.data is None) == (self.url is None):
            raise ValueError("Button needs exactly one of `data` or `url`")
        d = {"text": self.text}
        if self.data is not None:
            d["data"] = self.data
        else:
            d["url"] = self.url  # type: ignore[assignment]
        return d


Buttons = Sequence[Sequence[Union[Button, Dict[str, str]]]]


def normalize_buttons(buttons: Any) -> Optional[List[List[Dict[str, str]]]]:
    if buttons is None:
        return None
    rows = list(buttons)
    if rows and not isinstance(rows[0], (list, tuple)):  # flat list -> one row
        rows = [rows]
    return [[b.to_dict() if isinstance(b, Button) else dict(b) for b in row] for row in rows]


@dataclass
class Poll:
    options: List[str]
    multi: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"options": list(self.options), "multi": self.multi}


@dataclass
class BotCommand:
    command: str
    description: str

    def to_dict(self) -> Dict[str, str]:
        return {"command": self.command, "description": self.description}


# ----------------------------------------------------------------- incoming --

@dataclass
class User:
    nick: str = ""
    display: str = ""

    @classmethod
    def from_raw(cls, raw: Optional[Dict[str, Any]]) -> Optional["User"]:
        if not raw:
            return None
        return cls(nick=raw.get("nick", ""), display=raw.get("display", ""))


@dataclass
class SentMessage:
    """What ``send_message`` returns. ``raw`` holds the full server response."""
    bot: "Bot" = field(repr=False)
    chat_id: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> Any:
        r = self.raw
        for key in ("id", "message_id"):
            if key in r:
                return r[key]
        inner = r.get("message")
        if isinstance(inner, dict):
            return inner.get("id", inner.get("message_id"))
        return None

    async def edit(self, text: Optional[str] = None, buttons: Any = None) -> Dict[str, Any]:
        return await self.bot.edit_message(self.chat_id, self.id, text, buttons)

    async def delete(self) -> Dict[str, Any]:
        return await self.bot.delete_message(self.chat_id, self.id)

    async def react(self, emoji: str = "") -> Dict[str, Any]:
        return await self.bot.react(self.chat_id, self.id, emoji)


@dataclass
class Event:
    """Generic update. Unknown ``kind`` values from the server end up here."""
    id: int = 0
    kind: str = ""
    created: int = 0
    channel: Optional[str] = None
    from_user: Optional[User] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    bot: Optional["Bot"] = field(default=None, repr=False)

    _BASE = ("id", "kind", "created", "channel", "from_user", "raw", "bot")

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], bot: Optional["Bot"] = None) -> "Event":
        kw = {
            f.name: raw[f.name]
            for f in fields(cls)
            if f.name not in cls._BASE and raw.get(f.name) is not None
        }
        return cls(
            id=raw.get("id", 0),
            kind=raw.get("kind", ""),
            created=raw.get("created", 0),
            channel=raw.get("channel"),
            from_user=User.from_raw(raw.get("from")),
            raw=raw,
            bot=bot,
            **kw,
        )

    def _need_bot(self) -> "Bot":
        if self.bot is None:
            raise RuntimeError("This event is not bound to a Bot")
        return self.bot


@dataclass
class Message(Event):
    """Someone wrote to the bot in DM."""
    chat_id: int = 0
    message_id: int = 0
    text: str = ""
    media: List[Any] = field(default_factory=list)
    command: Optional[str] = None  # "start" for "/start hello"

    @property
    def command_args(self) -> str:
        if not self.command:
            return ""
        parts = self.text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""

    async def answer(self, text: Optional[str] = None, **kwargs: Any) -> SentMessage:
        return await self._need_bot().send_message(self.chat_id, text, **kwargs)

    reply = answer  # the API has no "reply to message" in DMs, so these are the same

    async def react(self, emoji: str) -> Dict[str, Any]:
        return await self._need_bot().react(self.chat_id, self.message_id, emoji)


@dataclass
class Callback(Event):
    """Someone pressed a button under a bot message."""
    chat_id: int = 0
    message_id: int = 0
    data: str = ""

    async def answer(self, text: Optional[str] = None, **kwargs: Any) -> SentMessage:
        return await self._need_bot().send_message(self.chat_id, text, **kwargs)

    async def edit_message(self, text: Optional[str] = None, buttons: Any = None) -> Dict[str, Any]:
        return await self._need_bot().edit_message(self.chat_id, self.message_id, text, buttons)

    async def delete_message(self) -> Dict[str, Any]:
        return await self._need_bot().delete_message(self.chat_id, self.message_id)


@dataclass
class Comment(Event):
    """Someone replied in a thread under the bot's post. ``post_id`` is the comment's own id."""
    post_id: int = 0
    reply_to: Optional[int] = None
    root_id: Optional[int] = None
    text: str = ""

    async def reply(self, text: str, **kwargs: Any) -> Dict[str, Any]:
        if self.channel and "channel" not in kwargs:
            kwargs["channel"] = self.channel
        return await self._need_bot().create_post(text, reply_to=self.post_id, **kwargs)


@dataclass
class Follow(Event):
    """Someone followed the bot."""

    async def follow_back(self) -> Dict[str, Any]:
        if not self.from_user:
            raise RuntimeError("No user in follow event")
        return await self._need_bot().follow(self.from_user.nick)


_KIND_MAP = {"message": Message, "button": Callback, "comment": Comment, "follow": Follow}


def parse_update(raw: Dict[str, Any], bot: Optional["Bot"] = None) -> Event:
    return _KIND_MAP.get(raw.get("kind", ""), Event).from_raw(raw, bot)
