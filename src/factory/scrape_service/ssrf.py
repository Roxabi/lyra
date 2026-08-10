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


def _effective_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> (
    ipaddress.IPv4Address | ipaddress.IPv6Address
):
    """Unwrap IPv4-mapped IPv6 so policy applies to the embedded v4."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if *ip* (after mapped unwrap) is non-global or Tailscale CGNAT."""
    eff = _effective_ip(ip)
    if (
        eff.is_private
        or eff.is_loopback
        or eff.is_link_local
        or eff.is_multicast
        or eff.is_reserved
        or eff.is_unspecified
        or not eff.is_global
    ):
        return True
    if isinstance(eff, ipaddress.IPv4Address) and eff in _TAILSCALE_NET:
        return True
    return False


async def hostname_is_blocked(hostname: str) -> bool:
    """True if *hostname* resolves to any non-global / Tailscale CGNAT address.

    Fail-closed on DNS errors (reject unresolvable).
    """
    try:
        results = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror as exc:
        raise SsrfRejected("unresolvable") from exc

    if not results:
        raise SsrfRejected("unresolvable")

    for _family, _type, _proto, _canon, sockaddr in results:
        addr_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        if ip_is_blocked(ip):
            return True
    return False


async def assert_url_safe(url: str) -> str:
    """Validate *url* for scrape; return stripped https URL or raise SsrfRejected."""
    raw = require_https_url(url)
    host = urlparse(raw).hostname or ""
    # Literal IP in hostname — check without DNS
    try:
        lit = ipaddress.ip_address(host)
        if ip_is_blocked(lit):
            raise SsrfRejected("private_ip")
        return raw
    except ValueError:
        pass
    if await hostname_is_blocked(host):
        raise SsrfRejected("private_ip")
    return raw
