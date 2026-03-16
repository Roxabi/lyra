"""Module-level admin user IDs registry.

Deprecated: prefer ``msg.is_admin`` (set by Authenticator.resolve() during
normalize). This module is kept for backward compat in code that does not
have access to an InboundMessage (e.g. startup wiring).

Usage (legacy):
    from lyra.core.admin import is_admin, set_admin_user_ids
    set_admin_user_ids(admin_ids)   # at startup
    if not is_admin(user_id): ...   # when no InboundMessage available

Preferred:
    if not msg.is_admin: ...        # when InboundMessage is available
"""

from __future__ import annotations

_admin_user_ids: frozenset[str] = frozenset()


def get_admin_user_ids() -> frozenset[str]:
    """Return the current set of admin user IDs."""
    return _admin_user_ids


def set_admin_user_ids(ids: set[str] | frozenset[str]) -> None:
    """Replace the admin user IDs set (call once at startup)."""
    global _admin_user_ids
    _admin_user_ids = frozenset(ids)


def is_admin(user_id: str | None) -> bool:
    """Return True if user_id is in the admin set."""
    return bool(user_id and user_id in _admin_user_ids)
