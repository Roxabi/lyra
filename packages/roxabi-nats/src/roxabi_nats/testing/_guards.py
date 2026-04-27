"""Shared testing guards for NATS test doubles.

Migrated from roxabi_contracts._testing_guards per ADR-059 V6.
roxabi_contracts._testing_guards re-exports from here for the deprecation window.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

__all__ = [
    "ALLOWED_LOOPBACK_HOSTS",
    "_assert_not_production",
    "_assert_loopback_url",
]

ALLOWED_LOOPBACK_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
)


def _assert_not_production(cls_name: str) -> None:
    """Guard 2 — raises RuntimeError when LYRA_ENV=production (case-insensitive)."""
    if os.environ.get("LYRA_ENV", "").casefold() == "production":
        raise RuntimeError(f"{cls_name} cannot run in production")


def _assert_loopback_url(url: str) -> None:
    """Guard 3 — raises ValueError when the URL hostname is not loopback."""
    host = urlparse(url).hostname
    if host not in ALLOWED_LOOPBACK_HOSTS:
        raise ValueError(
            f"loopback NATS URL required — refusing host {host!r}; "
            f"allowed: {sorted(ALLOWED_LOOPBACK_HOSTS)}"
        )
