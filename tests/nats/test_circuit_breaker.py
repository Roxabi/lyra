"""Tests for NatsCircuitBreaker."""

from __future__ import annotations

import threading
import time

import pytest

from lyra.nats.circuit_breaker import NatsCircuitBreaker


class TestNatsCircuitBreaker:
    def test_closed_by_default(self) -> None:
        # Arrange / Act
        cb = NatsCircuitBreaker()
        # Assert
        assert cb.is_open() is False

    def test_opens_after_threshold(self) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=3)
        # Act
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        # Assert
        assert cb.is_open() is True

    def test_single_failure_does_not_open(self) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=3)
        # Act
        cb.record_failure()
        cb.record_failure()
        # Assert — 2 failures with threshold=3 → still closed
        assert cb.is_open() is False

    def test_closed_after_success(self) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open() is True
        # Act
        cb.record_success()
        # Assert
        assert cb.is_open() is False
        assert cb._failures == 0

    def test_half_open_after_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open() is True
        # Act — monkeypatch monotonic so the timeout window has passed
        monkeypatch.setattr(
            "lyra.nats.circuit_breaker.time.monotonic",
            lambda: cb._open_until + 1.0,
        )
        # Assert — circuit is half-open (treated as closed — probe allowed)
        assert cb.is_open() is False

    def test_reopens_on_half_open_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange — open the circuit then advance past recovery timeout
        cb = NatsCircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        open_until_before = cb._open_until
        monkeypatch.setattr(
            "lyra.nats.circuit_breaker.time.monotonic",
            lambda: open_until_before + 1.0,
        )
        assert cb.is_open() is False  # half-open
        # Act — failure during half-open probe
        cb.record_failure()
        # Assert — circuit re-opens (_failures is still ≥ threshold)
        assert cb._open_until > open_until_before

    def test_custom_threshold(self) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=1)
        # Act
        cb.record_failure()
        # Assert
        assert cb.is_open() is True

    def test_custom_recovery_timeout(self) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=1, recovery_timeout=5.0)
        before = time.monotonic()
        # Act
        cb.record_failure()
        after = time.monotonic()
        # Assert — _open_until is approximately now + 5.0
        assert cb._open_until >= before + 5.0
        assert cb._open_until <= after + 5.0 + 0.1  # small tolerance

    def test_thread_safety(self) -> None:
        # Arrange
        cb = NatsCircuitBreaker(failure_threshold=100)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(10):
                    cb.record_failure()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        # Act
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert — no crashes, failure count is consistent
        assert errors == []
        assert cb._failures <= 100  # may have opened + not incremented further
