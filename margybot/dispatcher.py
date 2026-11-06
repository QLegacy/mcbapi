from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union

from .client import Bot
from .errors import Forbidden, MargyError, Unauthorized
from .filters import MagicFilter
from .types import Event, parse_update

log = logging.getLogger("margybot")

_KINDS = ("message", "button", "comment", "follow")


def _kwargs_for(cb: Callable[..., Any], data: Dict[str, Any], skip: int = 0) -> Dict[str, Any]:
    """Pick from ``data`` only the keyword args the callback actually declares."""
    params = list(inspect.signature(cb).parameters.values())[skip:]
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return dict(data)
    return {p.name: data[p.name] for p in params if p.name in data}


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class _Handler:
    def __init__(self, callback: Callable[..., Any], filters: tuple):
        self.callback = callback
        self.filters = filters

    async def check(self, event: Event) -> Optional[Dict[str, Any]]:
        extra: Dict[str, Any] = {}
        for f in self.filters:
            res = f.resolve(event) if isinstance(f, MagicFilter) else await _maybe_await(f(event))
            if not res:
                return None
            if isinstance(res, dict):
                extra.update(res)
        return extra

    async def call(self, event: Event, data: Dict[str, Any]) -> Any:
        return await _maybe_await(self.callback(event, **_kwargs_for(self.callback, data, skip=1)))


def _wrap(mw: Callable[..., Any], nxt: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    async def inner(event: Event, data: Dict[str, Any]) -> Any:
        return await _maybe_await(mw(nxt, event, data))
    return inner


class Router:
    """Group of handlers. Include into a Dispatcher (or another Router)."""

    def __init__(self, name: Optional[str] = None):
        self.name = name or hex(id(self))
        self._handlers: Dict[str, List[_Handler]] = {}
        self._sub: List["Router"] = []
        self._middlewares: List[Callable[..., Any]] = []
        self._startup: List[Callable[..., Any]] = []
        self._shutdown: List[Callable[..., Any]] = []
        self._errors: List[Callable[..., Any]] = []

    # --- registration ---------------------------------------------------------
    def on(self, kind: str, *filters: Any) -> Callable[[Callable], Callable]:
        def deco(cb: Callable) -> Callable:
            self._handlers.setdefault(kind, []).append(_Handler(cb, filters))
            return cb
        return deco

    def message(self, *filters: Any):  # DM to the bot
        return self.on("message", *filters)

    def button(self, *filters: Any):  # button pressed
        return self.on("button", *filters)

    callback_query = button  # aiogram-style alias

    def comment(self, *filters: Any):  # reply under the bot's post
        return self.on("comment", *filters)

    def follow(self, *filters: Any):
        return self.on("follow", *filters)

    def middleware(self, cb: Callable[..., Any]) -> Callable[..., Any]:
        """``async def mw(handler, event, data): ... return await handler(event, data)``"""
        self._middlewares.append(cb)
        return cb

    def on_startup(self, cb: Callable[..., Any]) -> Callable[..., Any]:
        self._startup.append(cb)
        return cb

    def on_shutdown(self, cb: Callable[..., Any]) -> Callable[..., Any]:
        self._shutdown.append(cb)
        return cb

    def error(self) -> Callable[[Callable], Callable]:
        """``async def h(event, exception)`` — called when a handler raises."""
        def deco(cb: Callable) -> Callable:
            self._errors.append(cb)
            return cb
        return deco

    def include_router(self, router: "Router") -> "Router":
        if router is self:
            raise ValueError("Router can't include itself")
        self._sub.append(router)
        return router

    # --- internals ------------------------------------------------------------
    def _walk(self):
        yield self
        for r in self._sub:
            yield from r._walk()

    async def _feed(self, event: Event, data: Dict[str, Any], mws: List[Callable[..., Any]]) -> bool:
        mws = mws + self._middlewares
        for h in self._handlers.get(event.kind, []):
            extra = await h.check(event)
            if extra is None:
                continue
            call: Callable[..., Awaitable[Any]] = h.call
            for mw in reversed(mws):
                call = _wrap(mw, call)
            await call(event, {**data, **extra})
            return True
        for r in self._sub:
            if await r._feed(event, data, mws):
                return True
        return False


class _KeyedLocks:
    """Serialize events of one chat (keeps order), run different chats in parallel."""

    def __init__(self) -> None:
        self._locks: Dict[Any, List[Any]] = {}

    @contextlib.asynccontextmanager
    async def __call__(self, key: Any):
        if key is None:
            yield
            return
        entry = self._locks.get(key)
        if entry is None:
            entry = self._locks[key] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self._locks.pop(key, None)


class Dispatcher(Router):
    """Root router + long-polling loop.

        dp = Dispatcher()
        @dp.message(Command("start"))
        async def start(m: Message): await m.answer("hi")
        asyncio.run(dp.start_polling(Bot(KEY)))
    """

    def __init__(self, **workflow_data: Any):
        super().__init__("dispatcher")
        self.workflow_data = workflow_data
        self._locks = _KeyedLocks()
        self._tasks: Set["asyncio.Task[Any]"] = set()
        self._stop: Optional[asyncio.Event] = None

    # --- processing one update -----------------------------------------------
    async def feed_update(self, bot: Bot, event: Event) -> bool:
        data = {"bot": bot, "dispatcher": self, "event": event, **self.workflow_data}
        key = None
        if getattr(event, "chat_id", None):
            key = ("chat", event.chat_id)  # type: ignore[attr-defined]
        elif getattr(event, "root_id", None):
            key = ("thread", event.root_id)  # type: ignore[attr-defined]
        async with self._locks(key):
            try:
                return await self._feed(event, data, [])
            except Exception as exc:  # noqa: BLE001
                if not await self._handle_error(event, exc, data):
                    log.exception("unhandled error in handler for %s #%s", event.kind, event.id)
                return True

    async def _handle_error(self, event: Event, exc: Exception, data: Dict[str, Any]) -> bool:
        handled = False
        for r in self._walk():
            for cb in r._errors:
                try:
                    await _maybe_await(cb(event, exc, **_kwargs_for(cb, data, skip=2)))
                    handled = True
                except Exception:  # noqa: BLE001
                    log.exception("error handler failed")
        return handled

    async def _hooks(self, attr: str, bot: Bot) -> None:
        data = {"bot": bot, "dispatcher": self, **self.workflow_data}
        for r in self._walk():
            for cb in getattr(r, attr):
                try:
                    await _maybe_await(cb(**_kwargs_for(cb, data)))
                except Exception:  # noqa: BLE001
                    log.exception("%s hook failed", attr)

    # --- polling ----------------------------------------------------------------
    def stop_polling(self) -> None:
        if self._stop is not None:
            self._stop.set()

    @staticmethod
    def _load_offset(path: Optional[Path]) -> int:
        if path and path.exists():
            with contextlib.suppress(ValueError, OSError):
                return int(path.read_text().strip() or 0)
        return 0

    @staticmethod
    def _save_offset(path: Optional[Path], value: int) -> None:
        if path is None:
            return
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(str(value))
        os.replace(tmp, path)

    async def start_polling(
        self,
        bot: Bot,
        *,
        timeout: int = 25,
        offset_file: Union[None, str, "os.PathLike[str]"] = None,
        skip_updates: bool = False,
        handle_signals: bool = True,
        close_bot: bool = True,
    ) -> None:
        """Run until ``stop_polling()`` / Ctrl-C.

        ``offset_file``: where to keep the last processed id, so a restart doesn't
        replay the 7 days of history the server stores.
        ``skip_updates``: ignore everything that arrived while the bot was offline.
        """
        path = Path(offset_file) if offset_file else None
        after = self._load_offset(path)
        self._stop = stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed: List[int] = []
        if handle_signals:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                    loop.add_signal_handler(sig, stop.set)
                    installed.append(sig)

        try:
            await self._hooks("_startup", bot)
            if skip_updates and after == 0:
                while True:
                    resp = await bot.get_updates(after=after, timeout=0)
                    nxt = resp.get("next", after)
                    if not resp.get("updates") or nxt == after:
                        after = nxt
                        break
                    after = nxt
                self._save_offset(path, after)
            log.info("polling started (proxy: %s)", bot.proxy or "none")

            failures = 0
            while not stop.is_set():
                poll = asyncio.ensure_future(bot.get_updates(after=after, timeout=timeout))
                stopper = asyncio.ensure_future(stop.wait())
                await asyncio.wait({poll, stopper}, return_when=asyncio.FIRST_COMPLETED)
                stopper.cancel()
                if not poll.done():
                    poll.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await poll
                    break
                try:
                    resp = poll.result()
                except (Unauthorized, Forbidden):
                    log.error("the server rejected the bot key; stopping")
                    raise
                except MargyError as exc:
                    failures += 1
                    delay = min(2 ** failures, 30)
                    log.warning("polling failed (%s); retry in %ds", exc, delay)
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(stop.wait(), delay)
                    continue
                failures = 0
                for raw in resp.get("updates", []):
                    event = parse_update(raw, bot)
                    task = asyncio.ensure_future(self.feed_update(bot, event))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                nxt = resp.get("next", after)
                if nxt != after:
                    after = nxt
                    self._save_offset(path, after)
        finally:
            for sig in installed:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await self._hooks("_shutdown", bot)
            if close_bot:
                await bot.close()
            self._stop = None
            log.info("polling stopped")
