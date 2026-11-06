from __future__ import annotations

from typing import Any, Optional


class MargyError(Exception):
    """Base class for all margybot errors."""


class NetworkError(MargyError):
    """Connection, proxy or timeout problem (all retries exhausted)."""


class APIError(MargyError):
    """The server answered with an error status."""

    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.payload = payload


class BadRequest(APIError):  # 400
    pass


class Unauthorized(APIError):  # 401: no key / wrong key
    pass


class Forbidden(APIError):  # 403: not allowed, bot disabled, author banned
    pass


class NotFound(APIError):  # 404
    pass


class RateLimited(APIError):  # 429
    def __init__(self, status: int, message: str, payload: Any = None,
                 retry_after: Optional[float] = None):
        super().__init__(status, message, payload)
        self.retry_after = retry_after


class ServiceUnavailable(APIError):
    """5xx or a non-JSON 403/503 (usually Cloudflare during an attack). Retry later."""
