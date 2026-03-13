"""Module-level admin user IDs registry.

Plugins and other components that need to verify admin status call
is_admin() without needing to thread admin_user_ids through every layer.

Usage:
    from lyra.core.admin import is_admin, set_admin_user_ids

    # At startup (in __main__.py):
    set_admin_user_ids(admin_ids)

    # In a plugin handler:
    if not is_admin(msg.user_id):
        return Response(content="Admin-only.")
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
