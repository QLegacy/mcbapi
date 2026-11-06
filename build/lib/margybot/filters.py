"""Filters: ``Command("start")`` and the aiogram-like magic ``F``.

    @router.message(F.text == "hi")
    @router.message(F.text.startswith("/echo"))
    @router.message(F.text.regexp(r"^\\d+$"))
    @router.button(F.data.in_({"yes", "no"}))
    @router.message(F.media)                      # has photos
    @router.message(~F.command, F.text)           # plain text, no command
    @router.message(F.from_user.nick == "anna")
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable


class MagicFilter:
    def __init__(self, fn: Callable[[Any], Any] = lambda e: e):
        self._fn = fn

    def resolve(self, obj: Any) -> Any:
        try:
            return self._fn(obj)
        except Exception:
            return None

    # attribute / call chains: F.text.startswith("x")
    def __getattr__(self, name: str) -> "MagicFilter":
        if name.startswith("__"):
            raise AttributeError(name)
        fn = self._fn
        return MagicFilter(lambda o: getattr(fn(o), name, None))

    def __call__(self, *args: Any, **kwargs: Any) -> "MagicFilter":
        fn = self._fn

        def call(o: Any) -> Any:
            target = fn(o)
            return target(*args, **kwargs) if callable(target) else None

        return MagicFilter(call)

    def _bin(self, other: Any, op: Callable[[Any, Any], Any]) -> "MagicFilter":
        fn = self._fn
        return MagicFilter(lambda o: op(fn(o), other.resolve(o) if isinstance(other, MagicFilter) else other))

    def __eq__(self, other: Any) -> "MagicFilter":  # type: ignore[override]
        return self._bin(other, lambda a, b: a == b)

    def __ne__(self, other: Any) -> "MagicFilter":  # type: ignore[override]
        return self._bin(other, lambda a, b: a != b)

    def __lt__(self, other: Any) -> "MagicFilter":
        return self._bin(other, lambda a, b: a is not None and a < b)

    def __gt__(self, other: Any) -> "MagicFilter":
        return self._bin(other, lambda a, b: a is not None and a > b)

    def __and__(self, other: "MagicFilter") -> "MagicFilter":
        return self._bin(other, lambda a, b: bool(a) and bool(b))

    def __or__(self, other: "MagicFilter") -> "MagicFilter":
        return self._bin(other, lambda a, b: bool(a) or bool(b))

    def __invert__(self) -> "MagicFilter":
        fn = self._fn
        return MagicFilter(lambda o: not fn(o))

    __hash__ = None  # type: ignore[assignment]

    def in_(self, values: Iterable[Any]) -> "MagicFilter":
        vals = set(values)
        fn = self._fn
        return MagicFilter(lambda o: fn(o) in vals)

    def contains(self, item: Any) -> "MagicFilter":
        fn = self._fn
        return MagicFilter(lambda o: (v := fn(o)) is not None and item in v)

    def regexp(self, pattern: str, flags: int = 0) -> "MagicFilter":
        rx = re.compile(pattern, flags)
        fn = self._fn

        def check(o: Any) -> Any:
            v = fn(o)
            return rx.search(v) if isinstance(v, str) else None

        return MagicFilter(check)


F = MagicFilter()


class Command:
    """Matches DMs with ``/command`` (``Message.command`` set by the server)."""

    def __init__(self, *commands: str, ignore_case: bool = True):
        self._ic = ignore_case
        self.commands = {(c.lstrip("/").lower() if ignore_case else c.lstrip("/")) for c in commands}

    def __call__(self, event: Any) -> bool:
        cmd = getattr(event, "command", None)
        if not cmd:
            return False
        return (cmd.lower() if self._ic else cmd) in self.commands
