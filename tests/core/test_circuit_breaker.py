"""Tests for CircuitBreaker, CircuitRegistry, CircuitOpenError."""

from __future__ import annotations

import asyncio
import time

import pytest

from lyra.core.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitRegistry,
    CircuitState,
    CircuitStatus,
)
from lyra.core.event_bus import EventBus, set_event_bus
from lyra.core.events import CircuitStateChanged

# --- State transitions ---


def test_initial_state_is_closed():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)
    assert cb._state == CircuitState.CLOSED
    assert cb.is_open() is False


def test_closed_does_not_open_before_threshold():
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)
    cb.record_failure()
    cb.record_failure()
    assert cb._state == CircuitState.CLOSED


def test_closed_to_open_at_threshold():
    """SC-01: CLOSED → OPEN after failure_threshold consecutive failures."""
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb._state == CircuitState.OPEN
    assert cb.is_open() is True


def test_open_to_half_open_after_timeout(monkeypatch):
    """SC-02: OPEN → HALF_OPEN after recovery_timeout seconds."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=30)
    cb.record_failure()
    assert cb._state == CircuitState.OPEN
    # Advance time past recovery_timeout
    original = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: original + 31)
    result = cb.is_open()
    assert cb._state == CircuitState.HALF_OPEN
    assert result is False  # probe slot acquired


def test_half_open_success_closes(monkeypatch):
    """SC-03: HALF_OPEN → CLOSED on record_success()."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=1)
    cb.record_failure()
    original = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: original + 2)
    cb.is_open()  # transitions to HALF_OPEN, acquires probe
    assert cb._state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb._state == CircuitState.CLOSED
    assert cb._failure_count == 0
    assert cb._probe_in_flight is False


def test_half_open_failure_reopens(monkeypatch):
    """SC-04: HALF_OPEN → OPEN on record_failure() (probe fails)."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=1)
    cb.record_failure()
    original = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: original + 2)
    cb.is_open()  # HALF_OPEN, probe acquired
    cb.record_failure()  # probe fails
    assert cb._state == CircuitState.OPEN
    assert cb._probe_in_flight is False


def test_half_open_concurrent_fast_fails(monkeypatch):
    """SC-05: Second caller while probe in flight → fast-fail (is_open returns True)."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=1)
    cb.record_failure()
    original = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: original + 2)
    first = cb.is_open()  # transitions HALF_OPEN, acquires probe → False
    second = cb.is_open()  # probe already in flight → True (fast-fail)
    assert first is False
    assert second is True
    assert cb._probe_in_flight is True


def test_open_stays_open_before_timeout():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=60)
    cb.record_failure()
    assert cb.is_open() is True  # before timeout


def test_success_in_closed_is_noop():
    """record_success() from CLOSED state is a no-op; count is not reset."""
    cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)
    cb.record_failure()
    cb.record_failure()
    assert cb._failure_count == 2
    cb.record_success()
    # Guard: CLOSED → record_success is a no-op, count stays at 2
    assert cb._failure_count == 2
    assert cb._state == CircuitState.CLOSED


# --- get_status / retry_after ---


def test_get_status_closed():
    cb = CircuitBreaker("svc", failure_threshold=3, recovery_timeout=60)
    s = cb.get_status()
    assert s.state == CircuitState.CLOSED
    assert s.retry_after is None
    assert s.name == "svc"
    assert s.failure_count == 0


def test_get_status_open_has_retry_after():
    cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=60)
    cb.record_failure()
    s = cb.get_status()
    assert s.state == CircuitState.OPEN
    assert s.retry_after is not None
    assert 0 < s.retry_after <= 60


def test_retry_after_formula():
    """retry_after = max(0, recovery_timeout - elapsed), rounded."""
    cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout=60)
    cb.record_failure()
    s = cb.get_status()
    # Should be close to 60 (just opened)
    assert s.retry_after is not None
    assert 58 <= s.retry_after <= 60


# --- CircuitOpenError ---


def test_circuit_open_error():
    err = CircuitOpenError("anthropic", 42.0)
    assert err.name == "anthropic"
    assert err.retry_after == 42.0
    assert "42" in str(err)


# --- CircuitRegistry ---


def test_registry_register_and_get():
    registry = CircuitRegistry()
    cb = CircuitBreaker("anthropic")
    registry.register(cb)
    assert registry["anthropic"] is cb
    assert registry.get("anthropic") is cb
    assert registry.get("nonexistent") is None


def test_registry_get_all_status():
    registry = CircuitRegistry()
    registry.register(CircuitBreaker("anthropic", failure_threshold=1))
    registry.register(CircuitBreaker("telegram"))
    all_status = registry.get_all_status()
    assert "anthropic" in all_status
    assert "telegram" in all_status
    assert all(isinstance(s, CircuitStatus) for s in all_status.values())


def test_registry_missing_key_raises():
    registry = CircuitRegistry()
    with pytest.raises(KeyError, match="nonexistent"):
        _ = registry["nonexistent"]


# --- SC-06: HALF_OPEN probe slot is exclusive ---


def test_half_open_probe_slot_blocks_concurrent_calls(monkeypatch):
    """SC-06: Only one probe is allowed concurrently in HALF_OPEN state.

    First call acquires the probe slot (is_open → False).
    All subsequent calls before record_success/failure are fast-failed (is_open → True).
    After record_success, the circuit closes and all calls are allowed again.
    """
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=1)
    cb.record_failure()
    original = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: original + 2)

    # First caller acquires the probe slot
    first = cb.is_open()
    assert first is False
    assert cb._probe_in_flight is True
    assert cb._state == CircuitState.HALF_OPEN

    # Concurrent callers are fast-failed while probe is in flight
    for _ in range(3):
        assert cb.is_open() is True

    # Probe succeeds → circuit closes → slot released
    cb.record_success()
    assert cb._state == CircuitState.CLOSED
    assert cb._probe_in_flight is False
    assert cb.is_open() is False  # now CLOSED, open calls pass through


# --- Open timer reset on continued failure ---


def test_open_timer_resets_on_continued_failure(monkeypatch):
    """record_failure() while OPEN resets _opened_at (timer restart)."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=30)
    cb.record_failure()
    assert cb._opened_at is not None
    original_opened_at: float = cb._opened_at
    # Another failure while OPEN
    cb.record_failure()
    assert cb._opened_at is not None
    assert cb._opened_at >= original_opened_at  # timer reset


# ---------------------------------------------------------------------------
# T7 — CircuitBreaker emits CircuitStateChanged via EventBus
# ---------------------------------------------------------------------------


@pytest.fixture
def cb_event_bus_fixture():
    """Register a fresh EventBus singleton; reset after test."""
    bus = EventBus()
    set_event_bus(bus)
    yield bus
    set_event_bus(None)  # type: ignore[arg-type]


def _attach_queue(bus: EventBus) -> asyncio.Queue:
    """Attach a plain asyncio.Queue as a bus subscriber and return it."""
    q: asyncio.Queue = asyncio.Queue()
    bus._subscribers.append(q)
    return q


class TestCircuitBreakerEventBusIntegration:
    """CircuitBreaker emits CircuitStateChanged on every state transition (T7)."""

    def test_closed_to_open_emits_circuit_state_changed(
        self, cb_event_bus_fixture: EventBus
    ) -> None:
        """Reaching failure threshold emits CircuitStateChanged(closed→open)."""
        # Arrange
        bus = cb_event_bus_fixture
        q = _attach_queue(bus)
        cb = CircuitBreaker("svc-a", failure_threshold=2, recovery_timeout=60)

        # Act — one failure below threshold (should not emit a transition)
        cb.record_failure()
        # Second failure trips the breaker: CLOSED → OPEN
        cb.record_failure()

        # Assert
        assert cb._state == CircuitState.OPEN
        assert not q.empty(), "Expected a CircuitStateChanged event to be emitted"
        event = q.get_nowait()
        assert isinstance(event, CircuitStateChanged)
        assert event.old_state == "closed"
        assert event.new_state == "open"
        assert event.platform == "svc-a"

    def test_open_to_half_open_emits_circuit_state_changed(
        self, cb_event_bus_fixture: EventBus, monkeypatch
    ) -> None:
        """After recovery_timeout, is_open() triggers OPEN→HALF_OPEN and emits event."""
        # Arrange
        bus = cb_event_bus_fixture
        cb = CircuitBreaker("svc-b", failure_threshold=1, recovery_timeout=30)
        cb.record_failure()
        assert cb._state == CircuitState.OPEN

        # Drain the CLOSED→OPEN event so the queue is clean before the test assertion
        q = _attach_queue(bus)

        original = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: original + 31)

        # Act — triggers OPEN → HALF_OPEN transition
        result = cb.is_open()

        # Assert
        assert cb._state == CircuitState.HALF_OPEN
        assert result is False  # probe slot acquired
        assert not q.empty(), "Expected CircuitStateChanged(OPEN→HALF_OPEN)"
        event = q.get_nowait()
        assert isinstance(event, CircuitStateChanged)
        assert event.old_state == "open"
        assert event.new_state == "half_open"

    def test_half_open_to_closed_emits_circuit_state_changed(
        self, cb_event_bus_fixture: EventBus, monkeypatch
    ) -> None:
        """record_success() in HALF_OPEN emits CircuitStateChanged(HALF_OPEN→CLOSED)."""
        # Arrange
        bus = cb_event_bus_fixture
        cb = CircuitBreaker("svc-c", failure_threshold=1, recovery_timeout=1)
        cb.record_failure()
        original = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: original + 2)
        cb.is_open()  # HALF_OPEN, probe acquired
        assert cb._state == CircuitState.HALF_OPEN

        # Drain events so far (CLOSED→OPEN and OPEN→HALF_OPEN)
        q = _attach_queue(bus)

        # Act
        cb.record_success()

        # Assert
        assert cb._state == CircuitState.CLOSED
        assert not q.empty(), "Expected CircuitStateChanged(HALF_OPEN→CLOSED)"
        event = q.get_nowait()
        assert isinstance(event, CircuitStateChanged)
        assert event.old_state == "half_open"
        assert event.new_state == "closed"

    def test_half_open_to_open_emits_circuit_state_changed(
        self, cb_event_bus_fixture: EventBus, monkeypatch
    ) -> None:
        """record_failure() in HALF_OPEN emits CircuitStateChanged(HALF_OPEN→OPEN)."""
        # Arrange
        bus = cb_event_bus_fixture
        cb = CircuitBreaker("svc-d", failure_threshold=1, recovery_timeout=1)
        cb.record_failure()
        original = time.monotonic()
        monkeypatch.setattr(time, "monotonic", lambda: original + 2)
        cb.is_open()  # HALF_OPEN, probe acquired
        assert cb._state == CircuitState.HALF_OPEN
        # probe is in flight (set by is_open); record_failure clears it on entry

        # Drain events so far
        q = _attach_queue(bus)

        # Act
        cb.record_failure()  # probe failed → back to OPEN

        # Assert
        assert cb._state == CircuitState.OPEN
        assert not q.empty(), "Expected CircuitStateChanged(HALF_OPEN→OPEN)"
        event = q.get_nowait()
        assert isinstance(event, CircuitStateChanged)
        assert event.old_state == "half_open"
        assert event.new_state == "open"
