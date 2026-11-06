"""margybot — async aiogram-style library for Margy Chat bots (chat.narez.xyz)."""

__version__ = "0.1.0"

from .client import Bot, DEFAULT_BASE_URL  # noqa: E402
from .dispatcher import Dispatcher, Router  # noqa: E402
from .errors import (  # noqa: E402
    APIError,
    BadRequest,
    Forbidden,
    MargyError,
    NetworkError,
    NotFound,
    RateLimited,
    ServiceUnavailable,
    Unauthorized,
)
from .filters import Command, F, MagicFilter  # noqa: E402
from .proxy import parse_proxy  # noqa: E402
from .types import (  # noqa: E402
    BotCommand,
    Button,
    Callback,
    Comment,
    Event,
    Follow,
    Message,
    Poll,
    SentMessage,
    User,
)

__all__ = [
    "Bot", "Dispatcher", "Router", "F", "Command", "MagicFilter", "parse_proxy",
    "Button", "Poll", "BotCommand", "Message", "Callback", "Comment", "Follow",
    "Event", "User", "SentMessage", "DEFAULT_BASE_URL",
    "MargyError", "NetworkError", "APIError", "BadRequest", "Unauthorized",
    "Forbidden", "NotFound", "RateLimited", "ServiceUnavailable",
]
