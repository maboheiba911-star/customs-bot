"""Network helpers shared by the bot and the setup tools."""
from __future__ import annotations

import socket

_real_getaddrinfo = socket.getaddrinfo


def prefer_ipv4() -> None:
    """Resolve names to IPv4 addresses only (falls back to anything when a name has no IPv4).

    Some mobile and home networks route IPv6 to Telegram badly: connections open but answers never come.
    Turned on with FORCE_IPV4=1 in .env (the phone installer sets it when it detects the problem).
    """
    if getattr(socket.getaddrinfo, "_ipv4_only", False):
        return

    def getaddrinfo(host, *args, **kwargs):
        res = _real_getaddrinfo(host, *args, **kwargs)
        return [r for r in res if r[0] == socket.AF_INET] or res

    getaddrinfo._ipv4_only = True
    socket.getaddrinfo = getaddrinfo


def restore() -> None:
    socket.getaddrinfo = _real_getaddrinfo
