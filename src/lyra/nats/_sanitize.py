"""Allowlist-based sanitization for platform_meta at the NATS trust boundary.

Strips unknown and underscore-prefixed keys from platform_meta dicts after
NATS deserialization, preventing session hijacking via crafted messages.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PLATFORM_META_ALLOWLIST: frozenset[str] = frozenset({
    "guild_id",
    "channel_id",
    "message_id",
    "thread_id",
    "channel_type",
    "chat_id",
    "topic_id",
    "is_group",
    "thread_session_id",
})


def sanitize_platform_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Filter platform_meta to allowlisted keys only.

    Strips:
    - Keys not in PLATFORM_META_ALLOWLIST
    - Keys with leading underscore (internal-only, e.g. _session_update_fn)

    Stripped keys are logged at DEBUG level (key names only, not values).
    """
    stripped = [
        k for k in meta if k not in PLATFORM_META_ALLOWLIST or k.startswith("_")
    ]
    if stripped:
        log.debug("platform_meta: stripped keys %s", stripped)
    return {
        k: v
        for k, v in meta.items()
        if k in PLATFORM_META_ALLOWLIST and not k.startswith("_")
    }
