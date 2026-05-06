"""Async-aware rate limiter for the gh_token helper.

Provides RateLimiter — a min-interval gate that serialises concurrent
waiters via an internal asyncio.Lock, using an injected clock for
testability (no real sleeping in unit tests).
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable


class RateLimiter:
    """Enforce a minimum interval between consecutive releases.

    Concurrent awaiters are serialised: only one passes at a time, and
    each must wait ``min_interval_s`` seconds since the previous release
    before proceeding.

    Args:
        min_interval_s: Minimum seconds between successive :meth:`mark`
            calls.  Default 45 s leaves margin vs. GitHub's 1/min cap.
        clock: Callable that returns a monotonic float (seconds).
            Inject ``fake_clock.now`` in tests to advance virtual time.
    """

    def __init__(
        self,
        min_interval_s: float = 45.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = min_interval_s
        self._clock = clock
        self._last_release: float = -min_interval_s  # allow first call immediately
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        """Block until ``min_interval_s`` has elapsed since the last :meth:`mark`.

        Concurrent callers queue behind an internal lock so only one
        passes through at a time.
        """
        async with self._lock:
            remaining = self._min_interval - (self._clock() - self._last_release)
            if remaining > 0:
                await asyncio.sleep(remaining)

    def mark(self) -> None:
        """Record a release at the current clock time."""
        self._last_release = self._clock()
