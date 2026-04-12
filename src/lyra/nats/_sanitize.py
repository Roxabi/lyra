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


def _coerce_value(val: Any) -> Any:
    """Coerce platform_meta values to safe types and cap length.

    str/int/bool pass through (str truncated to MAX_META_VALUE_LEN).
    Everything else is coerced via str() and then truncated.
    """
    if isinstance(val, bool) or isinstance(val, int):
        return val
    if isinstance(val, str):
        if len(val) > MAX_META_VALUE_LEN:
            log.warning(
                "platform_meta: truncated oversized string value (len=%d, max=%d)",
                len(val),
                MAX_META_VALUE_LEN,
            )
            return val[:MAX_META_VALUE_LEN]
        return val
    coerced = str(val)
    if len(coerced) > MAX_META_VALUE_LEN:
        log.warning(
            "platform_meta: truncated oversized coerced value (len=%d, max=%d)",
            len(coerced),
            MAX_META_VALUE_LEN,
        )
        coerced = coerced[:MAX_META_VALUE_LEN]
    return coerced


def sanitize_platform_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Filter platform_meta to allowlisted keys only, and cap/coerce values.

    Strips:
    - Keys not in PLATFORM_META_ALLOWLIST
    - Keys with leading underscore (internal-only, e.g. _session_update_fn)

    Value validation on surviving keys:
    - str longer than MAX_META_VALUE_LEN → truncated
    - Non-(str|int|bool) → coerced via str() and capped

    Stripped keys are logged at DEBUG level (key names only, not values).
    """
    stripped = [
        k for k in meta if k not in PLATFORM_META_ALLOWLIST or k.startswith("_")
    ]
    if stripped:
        log.debug("platform_meta: stripped keys %s", stripped)
    return {
        k: _coerce_value(v)
        for k, v in meta.items()
        if k in PLATFORM_META_ALLOWLIST and not k.startswith("_")
    }
