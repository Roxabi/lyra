"""Abuse-floor rate limiter for the gh_token helper.

Provides RateLimiter — a min-interval gate that exists solely to catch
pathological mint churn (e.g., a buggy dispenser that mints on every read).
It is NOT a brake on legitimate demand: with 15-min token TTLs and normal
usage the floor is never hit. The ~10 s default means hitting it is a signal
that something is wrong. When the floor fires, a WARNING is emitted so the
condition is visible in logs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

log = logging.getLogger(__name__)


class RateLimiter:
    """Abuse floor: enforce a minimum interval between consecutive mint calls.

    Acts as a last-resort guard against pathological churn — if mint() is
    called more than once per ~10 s, something is misbehaving. Normal usage
    (lazy dispenser with 15-min TTL) will never reach this floor.

    Concurrent awaiters are serialised: only one passes at a time, and
    each must wait ``min_interval_s`` seconds since the previous release
    before proceeding.

    Args:
        min_interval_s: Minimum seconds between successive :meth:`mark`
            calls.  Default 10 s — hitting it means something is wrong.
        clock: Callable that returns a monotonic float (seconds).
            Inject ``fake_clock.now`` in tests to advance virtual time.
    """

    def __init__(
        self,
        min_interval_s: float = 10.0,
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
                log.warning(
                    "RateLimiter abuse floor stalled %.2fs (pathological mint churn)",
                    remaining,
                )
                await asyncio.sleep(remaining)

    def mark(self) -> None:
        """Record a release at the current clock time."""
        self._last_release = self._clock()
