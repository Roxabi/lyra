"""Circuit breaker implementation for LLM provider resilience.

Provides a state machine (CLOSED → OPEN → HALF_OPEN → CLOSED) that
tracks consecutive failures and temporarily rejects calls to a failing
provider, allowing it time to recover before probing again.

Usage::

    cb = CircuitBreaker(name="openai", failure_threshold=5, recovery_timeout=60)
    registry = CircuitRegistry()
    registry.register(cb)

    if cb.is_open():
        status = cb.get_status()
        raise CircuitOpenError(cb.name, status.retry_after or 0.0)
    try:
        result = await call_provider()
        cb.record_success()
    except SomeProviderError:
        cb.record_failure()
        raise
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(Enum):
    """Possible states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStatus:
    """Snapshot of a circuit breaker's current state."""

    name: str
    state: CircuitState
    failure_count: int
    retry_after: float | None  # seconds until HALF_OPEN probe; None when CLOSED


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str, retry_after: float) -> None:
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit '{name}' is open. Retry in {retry_after:.0f}s.")


class CircuitBreaker:
    """State machine that tracks failures and temporarily blocks a provider.

    The probe slot mechanism (``_probe_in_flight``) uses a plain bool rather
    than an asyncio.Lock because asyncio is single-threaded and ``is_open``
    is a synchronous method — there are no await points between the check and
    the set, so no race conditions are possible.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def is_open(self) -> bool:
        """Return True if the call should be rejected.

        Side-effects:
        - OPEN → HALF_OPEN transition when ``recovery_timeout`` has elapsed.
        - Sets ``_probe_in_flight = True`` for the first HALF_OPEN caller
          (granting it the probe slot).
        - A second HALF_OPEN caller while a probe is in flight returns True
          (fast-fail).
        """
        if self._state == CircuitState.CLOSED:
            return False

        if self._state == CircuitState.OPEN:
            if self._opened_at is not None:
                elapsed = time.monotonic() - self._opened_at
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    # fall through to HALF_OPEN logic
                else:
                    return True  # still within the open window

        # CircuitState.HALF_OPEN
        if self._probe_in_flight:
            return True  # another probe already in flight — fast-fail
        self._probe_in_flight = True  # claim the probe slot
        return False  # allow this call as the probe

    def record_success(self) -> None:
        """Transition HALF_OPEN → CLOSED and reset all counters."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        """Record a failure and update state accordingly.

        - CLOSED: increment count; trip to OPEN when ``failure_threshold`` is
          reached.
        - HALF_OPEN: probe failed → back to OPEN, reset timer.
        - OPEN: reset the recovery timer (continued failure).

        Always clears the probe flag.
        """
        self._probe_in_flight = False
        self._failure_count += 1
        now = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = now
        elif self._state == CircuitState.CLOSED:
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = now
        elif self._state == CircuitState.OPEN:
            self._opened_at = now  # reset the recovery timer

    def get_status(self) -> CircuitStatus:
        """Return a snapshot of the current circuit state.

        ``retry_after`` is the number of seconds remaining until a probe is
        allowed (only meaningful when OPEN or HALF_OPEN), or ``None`` when
        CLOSED.
        """
        retry_after: float | None = None
        if (
            self._state in (CircuitState.OPEN, CircuitState.HALF_OPEN)
            and self._opened_at is not None
        ):
            retry_after = max(
                0.0, self.recovery_timeout - (time.monotonic() - self._opened_at)
            )
        return CircuitStatus(
            name=self.name,
            state=self._state,
            failure_count=self._failure_count,
            retry_after=retry_after,
        )


class CircuitRegistry:
    """Central registry for named circuit breakers."""

    def __init__(self) -> None:
        self._circuits: dict[str, CircuitBreaker] = {}

    def register(self, cb: CircuitBreaker) -> None:
        """Register a circuit breaker under its name."""
        self._circuits[cb.name] = cb

    def __getitem__(self, name: str) -> CircuitBreaker:
        return self._circuits[name]

    def get(self, name: str) -> CircuitBreaker | None:
        """Return the named circuit breaker, or None if not registered."""
        return self._circuits.get(name)

    def get_all_status(self) -> dict[str, CircuitStatus]:
        """Return a status snapshot for every registered circuit breaker."""
        return {name: cb.get_status() for name, cb in self._circuits.items()}
