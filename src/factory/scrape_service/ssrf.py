"""SSRF guards for the scrape service (HTTPS-only, non-global IP reject).

Dual-check design: hub also pre-checks; this module re-validates at the
service boundary including redirect targets.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

# Tailscale CGNAT + classic private ranges covered by is_private / is_reserved.
_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


class SsrfRejected(ValueError):
    """URL rejected by SSRF policy."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def require_https_url(url: str) -> str:
    """Return stripped URL if scheme is https and host present; else raise."""
    raw = url.strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise SsrfRejected("https_only")
    if not parsed.hostname:
        raise SsrfRejected("missing_host")
    return raw


async def hostname_is_blocked(hostname: str) -> bool:
    """True if *hostname* resolves to any non-global / Tailscale CGNAT address."""
    try:
        results = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror:
        # Unresolvable — allow through; fetch will fail cleanly.
        return False

    for _family, _type, _proto, _canon, sockaddr in results:
        addr_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or ip in _TAILSCALE_NET
        ):
            return True
        # Explicit CGNAT check for IPv4 mapped edge cases
        if isinstance(ip, ipaddress.IPv4Address) and ip in _TAILSCALE_NET:
            return True
    return False


async def assert_url_safe(url: str) -> str:
    """Validate *url* for scrape; return stripped https URL or raise SsrfRejected."""
    raw = require_https_url(url)
    host = urlparse(raw).hostname or ""
    if await hostname_is_blocked(host):
        raise SsrfRejected("private_ip")
    return raw
