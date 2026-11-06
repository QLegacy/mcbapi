"""Proxy URL parsing.

Supported schemes:
    socks5://  socks5h://  socks4://  socks4a://  http://  https://

``socks5h`` / ``socks4a`` resolve DNS on the proxy side (like curl); plain
``socks5`` / ``socks4`` resolve locally. ``https://`` means an HTTP proxy that
is reached over plain TCP and tunnels HTTPS via CONNECT (what 99% of "https
proxies" are in practice).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlsplit

from aiohttp_socks import ProxyConnector, ProxyType

_SCHEMES = {
    "socks5": (ProxyType.SOCKS5, False, 1080),
    "socks5h": (ProxyType.SOCKS5, True, 1080),
    "socks4": (ProxyType.SOCKS4, False, 1080),
    "socks4a": (ProxyType.SOCKS4, True, 1080),
    "http": (ProxyType.HTTP, False, 8080),
    "https": (ProxyType.HTTP, False, 443),
}


@dataclass(frozen=True)
class ProxyConfig:
    url: str
    type: ProxyType
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    rdns: bool

    def connector(self) -> ProxyConnector:
        # must be called inside a running event loop
        return ProxyConnector(
            proxy_type=self.type,
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            rdns=self.rdns,
        )

    def __str__(self) -> str:  # never leak credentials into logs
        auth = "***@" if self.username else ""
        return f"{self.url.split('://', 1)[0]}://{auth}{self.host}:{self.port}"


def parse_proxy(url: str) -> ProxyConfig:
    raw = url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise ValueError(
            f"Unsupported proxy scheme {scheme!r}; use one of: {', '.join(_SCHEMES)}"
        )
    if not parts.hostname:
        raise ValueError(f"Proxy URL has no host: {url!r}")
    ptype, rdns, default_port = _SCHEMES[scheme]
    return ProxyConfig(
        url=raw,
        type=ptype,
        host=parts.hostname,
        port=parts.port or default_port,
        username=unquote(parts.username) if parts.username else None,
        password=unquote(parts.password) if parts.password else None,
        rdns=rdns,
    )
