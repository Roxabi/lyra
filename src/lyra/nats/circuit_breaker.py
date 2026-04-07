"""NatsCircuitBreaker — in-process circuit breaker for NATS voice adapter clients."""

from __future__ import annotations

import threading
import time


class NatsCircuitBreaker:
    """Thread-safe circuit breaker.

    Opens after *failure_threshold* consecutive failures.
    Stays open for *recovery_timeout* seconds, then allows one probe
    (half-open). Success → closed; failure → re-opens immediately.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failures = 0
        self._open_until: float = 0.0
        self._lock = threading.Lock()
        self._half_open_probe: bool = False

    def is_open(self) -> bool:
        """Return True if the circuit is open (calls should be blocked)."""
        with self._lock:
            if not self._open_until:
                return False
            if time.monotonic() < self._open_until:
                return True
            # Timer expired — half-open: allow exactly one probe
            if self._half_open_probe:
                return True
            self._half_open_probe = True
            return False

    def record_success(self) -> None:
        """Reset failure count and close the circuit."""
        with self._lock:
            self._failures = 0
            self._open_until = 0.0
            self._half_open_probe = False

    def record_failure(self) -> None:
        """Increment failure count; open the circuit once threshold is reached."""
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = time.monotonic() + self.recovery_timeout
                self._half_open_probe = False
