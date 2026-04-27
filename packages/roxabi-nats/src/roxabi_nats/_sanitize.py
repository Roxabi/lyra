"""Allowlist-based sanitization for platform_meta at the NATS trust boundary.

Strips unknown and underscore-prefixed keys from platform_meta dicts after
NATS deserialization, preventing session hijacking via crafted messages.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PLATFORM_META_ALLOWLIST: frozenset[str] = frozenset(
    {
        "guild_id",
        "channel_id",
        "message_id",
        "thread_id",
        "channel_type",
        "chat_id",
        "topic_id",
        "is_group",
        "thread_session_id",
    }
)

MAX_META_VALUE_LEN = 256


def _cap_value(val: str | int | bool) -> str | int | bool:
    """Cap string values at MAX_META_VALUE_LEN; pass scalars through."""
    if isinstance(val, str) and len(val) > MAX_META_VALUE_LEN:
        log.debug(
            "platform_meta: truncated oversized string value (len=%d, max=%d)",
            len(val),
            MAX_META_VALUE_LEN,
        )
        return val[:MAX_META_VALUE_LEN]
    return val


def sanitize_platform_meta(
    meta: dict[str, Any],
    *,
    allowlist: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Filter platform_meta to allowlisted keys and safe scalar values.

    Strips:
    - Keys not in the allowlist (defaults to ``PLATFORM_META_ALLOWLIST``)
    - Keys with leading underscore (internal-only, e.g. _session_update_fn)
    - Values that are not scalar (``str | int | bool``) — avoids the
      ``str({deep dict})`` memory-amplification path since the only
      legitimate platform_meta values are primitives.

    Value validation on surviving keys:
    - str longer than MAX_META_VALUE_LEN → truncated (logged at DEBUG)

    Stripped keys and dropped values are logged at DEBUG (key names only,
    never value content — avoids log-amplification from crafted messages).

    Pass ``allowlist`` to inject a platform-specific set instead of the
    hardcoded default (e.g. sourced from roxabi-contracts at call time).
    """
    active_allowlist = allowlist if allowlist is not None else PLATFORM_META_ALLOWLIST
    stripped = [
        k for k in meta if k not in active_allowlist or k.startswith("_")
    ]
    if stripped:
        log.debug("platform_meta: stripped keys %s", stripped)
    result: dict[str, Any] = {}
    for k, v in meta.items():
        if k not in active_allowlist or k.startswith("_"):
            continue
        if not isinstance(v, (str, int, bool)):
            log.debug(
                "platform_meta: dropped non-scalar value for key %r (type=%s)",
                k,
                type(v).__name__,
            )
            continue
        result[k] = _cap_value(v)
    return result
