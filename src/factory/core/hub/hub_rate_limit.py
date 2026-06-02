"""Sliding-window rate limiter for hub inbound messages."""

from __future__ import annotations

import time
from collections import deque

from ..messaging.message import InboundMessage


class RateLimiter:
    """Per-user sliding-window rate limiter.

    Rate limiting is per-user (not per-scope) to prevent rate-limit bypass
    by switching chats. Entries are removed when the deque empties.
    """

    def __init__(self, limit: int, window: int) -> None:
        self._limit = limit
        self._window = window
        # Maps (platform.value, bot_id, user_id) -> deque of timestamps.
        self._timestamps: dict[tuple[str, str, str], deque[float]] = {}

    def is_limited_by_key(self, key: tuple[str, str, str]) -> bool:
        """Sliding-window rate check for an arbitrary (platform, bot_id, user_id) key.

        Returns True when the user has exceeded the per-window limit.
        Appends a timestamp and returns False otherwise.
        """
        now = time.monotonic()
        window_start = now - self._window
        timestamps = self._timestamps.get(key)
        if timestamps is not None:
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            if not timestamps:
                del self._timestamps[key]
                timestamps = None
        if timestamps is not None and len(timestamps) >= self._limit:
            return True
        if timestamps is None:
            timestamps = deque()
            self._timestamps[key] = timestamps
        timestamps.append(now)
        return False

    def is_limited(self, msg: InboundMessage) -> bool:
        """Return True if this user has exceeded the per-window message limit."""
        # str() normalizes platform: InboundMessage.platform is str, not Platform enum
        key = (str(msg.platform), msg.bot_id, msg.user_id)
        return self.is_limited_by_key(key)
