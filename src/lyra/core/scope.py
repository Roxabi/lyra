"""Scope helpers — shared utilities for scope_id computation.

Adapters compute ``scope_id`` deterministically from platform-specific
routing data.  For shared spaces (groups, guild channels) the scope must
include the user identity so that each user gets their own pool.

The helper lives in ``core/`` because it is imported by multiple adapters;
it produces a value that flows *into* core (via ``RoutingKey``), not out of
it — ``RoutingKey.to_pool_id()`` treats ``scope_id`` as an opaque string.
"""

from __future__ import annotations


def user_scoped(scope_id: str, user_id: str) -> str:
    """Append user identity to a scope_id for shared-space isolation.

    Call this from an adapter's ``normalize()`` when the message originates
    from a shared space (Telegram group, Discord guild channel, etc.).

    **Not idempotent** — calling twice produces a corrupted scope_id.
    Callers must ensure this is called exactly once per shared-space message.

    >>> user_scoped("chat:42", "tg:user:1")
    'chat:42:user:tg:user:1'
    """
    return f"{scope_id}:user:{user_id}"
