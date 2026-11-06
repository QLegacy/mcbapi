from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, AsyncIterable, Dict, List, Optional, Sequence, Union

import aiohttp
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
try:  # base class of all python-socks errors (private module, hence the guard)
    from python_socks._errors import ProxyException
except ImportError:  # pragma: no cover
    from python_socks import ProxyError as ProxyException

from . import __version__
from .errors import (
    APIError,
    BadRequest,
    Forbidden,
    NetworkError,
    NotFound,
    RateLimited,
    ServiceUnavailable,
    Unauthorized,
)
from .proxy import ProxyConfig, parse_proxy
from .types import BotCommand, Poll, SentMessage, normalize_buttons

log = logging.getLogger("margybot")

DEFAULT_BASE_URL = "https://chat.narez.xyz/chat/api"

ProxyArg = Union[None, str, Sequence[str]]
PhotoSource = Union[bytes, bytearray, str, "os.PathLike[str]"]

_NETWORK_ERRORS = (
    aiohttp.ClientError, asyncio.TimeoutError, OSError,
    ProxyException, ProxyError, ProxyConnectionError, ProxyTimeoutError,
)


def _sniff_content_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _try_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _retry_after(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    return min(2 ** (attempt - 1), 20) + random.uniform(0, 0.3)


def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


class Bot:
    """Margy Chat bot client.

    >>> bot = Bot("mcb_123_key", proxy="socks5://user:pass@127.0.0.1:1080")
    >>> await bot.send_message(7, "hi")

    ``proxy`` may be one URL or a list of URLs. With a list, the next proxy is
    used automatically whenever the current one fails.
    """

    def __init__(
        self,
        token: str,
        *,
        proxy: ProxyArg = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 40.0,
        max_retries: int = 3,
        user_agent: Optional[str] = None,
    ):
        if not token:
            raise ValueError("Bot key is empty")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent or f"margybot/{__version__}"
        if proxy is None:
            urls: List[str] = []
        elif isinstance(proxy, str):
            urls = [proxy]
        else:
            urls = list(proxy)
        self._proxies: List[ProxyConfig] = [parse_proxy(u) for u in urls]
        self._proxy_idx = 0
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------ transport --

    @property
    def proxy(self) -> Optional[str]:
        """Currently active proxy (credentials hidden), or None."""
        return str(self._proxies[self._proxy_idx]) if self._proxies else None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = self._proxies[self._proxy_idx].connector() if self._proxies else None
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def _rotate_proxy(self) -> None:
        if len(self._proxies) < 2:
            return
        self._proxy_idx = (self._proxy_idx + 1) % len(self._proxies)
        log.info("switching to proxy %s", self.proxy)
        await self.close()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "Bot":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        data: Optional[bytes] = None,
        content_type: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> Any:
        """Low-level call; use it for endpoints the library doesn't wrap yet."""
        url = self.base_url + path
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if content_type:
            headers["Content-Type"] = content_type
        retries = self.max_retries if retries is None else retries
        client_timeout = aiohttp.ClientTimeout(total=timeout or self.timeout)
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._get_session().request(
                    method, url, params=params, json=json_body, data=data,
                    headers=headers, timeout=client_timeout,
                ) as resp:
                    status = resp.status
                    raw = await resp.read()
                    retry_after = _retry_after(resp.headers.get("Retry-After"))
            except _NETWORK_ERRORS as exc:
                if attempt > retries:
                    raise NetworkError(f"{method} {path}: {exc!r}") from exc
                log.warning("network error (%r), retry %d/%d", exc, attempt, retries)
                await self._rotate_proxy()
                await asyncio.sleep(_backoff(attempt))
                continue

            payload = _try_json(raw)
            if 200 <= status < 300:
                return payload if payload is not None else {}

            is_json = isinstance(payload, dict)
            message = (
                str(payload.get("error", payload)) if is_json
                else raw[:200].decode("utf-8", "replace").strip() or f"HTTP {status}"
            )
            # Cloudflare answers with 403/503 that is not JSON -> just try later
            transient = status in (500, 502, 503, 504) or (status in (403, 503) and not is_json)
            if (transient or status == 429) and attempt <= retries:
                delay = retry_after if retry_after is not None else _backoff(attempt)
                log.warning("HTTP %d on %s %s, retry in %.1fs", status, method, path, delay)
                await asyncio.sleep(delay)
                continue
            if status == 429:
                raise RateLimited(status, message, payload, retry_after)
            if transient:
                raise ServiceUnavailable(status, message, payload)
            cls = {400: BadRequest, 401: Unauthorized, 403: Forbidden, 404: NotFound}.get(status, APIError)
            raise cls(status, message, payload)

    # ---------------------------------------------------------------- media --

    async def upload_photo(self, source: PhotoSource, content_type: Optional[str] = None) -> Any:
        """Upload an image (bytes or file path). Returns the photo id for ``media=[...]``."""
        if isinstance(source, (bytes, bytearray)):
            blob = bytes(source)
        else:
            blob = await asyncio.get_running_loop().run_in_executor(
                None, Path(source).read_bytes
            )
        resp = await self.request(
            "POST", "/bot/photos", data=blob,
            content_type=content_type or _sniff_content_type(blob),
        )
        return resp["id"]

    async def _media(self, media: Optional[Sequence[Any]]) -> Optional[List[Any]]:
        """Items may be ready photo ids, or bytes / file paths (uploaded automatically)."""
        if not media:
            return None
        out: List[Any] = []
        for item in media:
            if isinstance(item, (bytes, bytearray, os.PathLike)):
                out.append(await self.upload_photo(item))  # type: ignore[arg-type]
            else:
                out.append(item)
        return out

    # ------------------------------------------------------------- bot info --

    async def me(self) -> Dict[str, Any]:
        return await self.request("GET", "/bot/me")

    async def set_commands(self, commands: Sequence[Union[BotCommand, tuple, Dict[str, str]]]) -> Dict[str, Any]:
        items = []
        for c in commands:
            if isinstance(c, BotCommand):
                items.append(c.to_dict())
            elif isinstance(c, tuple):
                items.append({"command": c[0], "description": c[1]})
            else:
                items.append(dict(c))
        return await self.request("POST", "/bot/commands", json_body={"commands": items})

    # --------------------------------------------------------------- updates --

    async def get_updates(self, after: int = 0, timeout: int = 25) -> Dict[str, Any]:
        """Long poll. Returns ``{"updates": [...], "next": int}``."""
        return await self.request(
            "GET", "/bot/updates",
            params={"after": after, "timeout": timeout},
            timeout=timeout + 15,
        )

    # ------------------------------------------------------------------- DMs --

    async def send_message(
        self,
        chat_id: int,
        text: Optional[str] = None,
        *,
        media: Optional[Sequence[Any]] = None,
        buttons: Any = None,
    ) -> SentMessage:
        body = _clean({
            "text": text,
            "media": await self._media(media),
            "buttons": normalize_buttons(buttons),
        })
        raw = await self.request("POST", f"/bot/chats/{chat_id}/messages", json_body=body)
        return SentMessage(bot=self, chat_id=chat_id, raw=raw if isinstance(raw, dict) else {"raw": raw})

    async def edit_message(
        self, chat_id: int, message_id: Any, text: Optional[str] = None, buttons: Any = None
    ) -> Dict[str, Any]:
        body = _clean({"text": text, "buttons": normalize_buttons(buttons)})
        return await self.request("PATCH", f"/bot/chats/{chat_id}/messages/{message_id}", json_body=body)

    async def delete_message(self, chat_id: int, message_id: Any) -> Dict[str, Any]:
        return await self.request("DELETE", f"/bot/chats/{chat_id}/messages/{message_id}")

    async def react(self, chat_id: int, message_id: Any, emoji: str = "") -> Dict[str, Any]:
        """Empty string removes the reaction."""
        return await self.request(
            "POST", f"/bot/chats/{chat_id}/messages/{message_id}/react", json_body={"emoji": emoji}
        )

    async def get_chats(self, channel: Optional[str] = None) -> Dict[str, Any]:
        return await self.request("GET", "/bot/chats", params=_clean({"channel": channel}))

    async def get_messages(self, chat_id: int, after: int = 0) -> Dict[str, Any]:
        """Up to 100 messages after id ``after``; each has ``mine`` (bot's or not)."""
        return await self.request("GET", f"/bot/chats/{chat_id}/messages", params={"after": after})

    async def stream_message(
        self,
        chat_id: int,
        chunks: AsyncIterable[str],
        *,
        placeholder: str = "Думаю…",
        interval: float = 2.0,
        buttons: Any = None,
        max_len: int = 2000,
    ) -> SentMessage:
        """Send a placeholder and keep editing it while ``chunks`` arrive (LLM streaming).

        Edits are throttled to one per ``interval`` seconds because all of a bot's
        requests share a 180/min limit. Text is cut at ``max_len`` (the DM limit).
        """
        sent = await self.send_message(chat_id, placeholder)
        buf, shown, last = "", placeholder, time.monotonic()
        async for chunk in chunks:
            buf += chunk
            if time.monotonic() - last >= interval and buf[:max_len] != shown:
                shown = buf[:max_len]
                await sent.edit(shown)
                last = time.monotonic()
        final = buf[:max_len]
        if final and (final != shown or buttons is not None):
            await sent.edit(final, buttons)
        return sent

    # ----------------------------------------------------------------- posts --

    async def create_post(
        self,
        text: str,
        *,
        media: Optional[Sequence[Any]] = None,
        reply_to: Optional[int] = None,
        channel: Optional[str] = None,
        markdown: bool = False,
        poll: Union[None, Poll, Dict[str, Any]] = None,
        quote_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Post, reply (``reply_to``), poll, channel post. Check ``hidden`` in the answer."""
        body = _clean({
            "text": text,
            "media": await self._media(media),
            "reply_to": reply_to,
            "channel": channel,
            "markdown": True if markdown else None,
            "poll": poll.to_dict() if isinstance(poll, Poll) else poll,
            "quote_id": quote_id,
        })
        return await self.request("POST", "/bot/posts", json_body=body)

    async def get_posts(self, at: int = 0, channel: Optional[str] = None) -> Dict[str, Any]:
        return await self.request("GET", "/bot/posts", params=_clean({"at": at, "channel": channel}))

    async def get_post(self, post_id: int, channel: Optional[str] = None) -> Dict[str, Any]:
        """``{"post": ..., "replies": [...]}`` — rebuild the tree via ``reply_to``."""
        return await self.request("GET", f"/bot/posts/{post_id}", params=_clean({"channel": channel}))

    async def delete_post(self, post_id: int, channel: Optional[str] = None) -> Dict[str, Any]:
        return await self.request("DELETE", f"/bot/posts/{post_id}", params=_clean({"channel": channel}))

    # ------------------------------------------------------------ followers --

    async def get_followers(self, at: int = 0) -> Dict[str, Any]:
        return await self.request("GET", "/bot/followers", params={"at": at})

    async def follow(self, nick: str, on: bool = True) -> Dict[str, Any]:
        """Follow back (only allowed if the user already follows the bot)."""
        body: Dict[str, Any] = {"nick": nick}
        if not on:
            body["on"] = False
        return await self.request("POST", "/bot/follow", json_body=body)
