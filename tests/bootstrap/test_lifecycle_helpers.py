"""Tests for shared lifecycle helpers."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.bootstrap.lifecycle.lifecycle_helpers import (
    close_safely,
    setup_signal_handlers,
    teardown_buses,
    teardown_dispatchers,
)


def test_setup_signal_handlers_registers_sigint_and_sigterm() -> None:
    """setup_signal_handlers registers handlers for both SIGINT and SIGTERM."""
    mock_loop = MagicMock()
    stop = asyncio.Event()

    _target = "lyra.bootstrap.lifecycle.lifecycle_helpers.asyncio.get_running_loop"
    with patch(_target, return_value=mock_loop):
        setup_signal_handlers(stop)

    calls = mock_loop.add_signal_handler.call_args_list
    signals_registered = {c.args[0] for c in calls}
    assert signal.SIGINT in signals_registered
    assert signal.SIGTERM in signals_registered


def test_setup_signal_handlers_uses_stop_event() -> None:
    """setup_signal_handlers wires the stop event's set method as the callback."""
    mock_loop = MagicMock()
    stop = asyncio.Event()

    _target = "lyra.bootstrap.lifecycle.lifecycle_helpers.asyncio.get_running_loop"
    with patch(_target, return_value=mock_loop):
        setup_signal_handlers(stop)

    for call in mock_loop.add_signal_handler.call_args_list:
        callback = call.args[1]
        assert callback == stop.set


@pytest.mark.asyncio
async def test_teardown_buses_calls_stop_on_each_bus() -> None:
    """teardown_buses calls .stop() on every bus passed in."""
    bus_a = AsyncMock()
    bus_b = AsyncMock()

    await teardown_buses(bus_a, bus_b)

    bus_a.stop.assert_awaited_once()
    bus_b.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_teardown_buses_no_buses_is_a_noop() -> None:
    """teardown_buses with no arguments completes without error."""
    await teardown_buses()


@pytest.mark.asyncio
async def test_teardown_dispatchers_calls_stop_on_each_dispatcher() -> None:
    """teardown_dispatchers calls .stop() on every dispatcher passed in."""
    d1 = AsyncMock()
    d2 = AsyncMock()
    d3 = AsyncMock()

    await teardown_dispatchers([d1, d2, d3])

    d1.stop.assert_awaited_once()
    d2.stop.assert_awaited_once()
    d3.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_teardown_dispatchers_empty_list_is_a_noop() -> None:
    """teardown_dispatchers with an empty list completes without error."""
    await teardown_dispatchers([])


@pytest.mark.asyncio
async def test_teardown_dispatchers_stop_order_matches_input() -> None:
    """teardown_dispatchers stops dispatchers in the order they are provided."""
    call_order: list[str] = []

    async def stop_d1() -> None:
        call_order.append("d1")

    async def stop_d2() -> None:
        call_order.append("d2")

    d1 = MagicMock()
    d1.stop = stop_d1
    d2 = MagicMock()
    d2.stop = stop_d2

    await teardown_dispatchers([d1, d2])

    assert call_order == ["d1", "d2"]


# ---------------------------------------------------------------------------
# TestCloseSafely — concurrent teardown with exception isolation
# ---------------------------------------------------------------------------


class TestCloseSafely:
    """close_safely() awaits all coros concurrently and isolates failures."""

    async def test_empty_coros_is_noop(self) -> None:
        """close_safely with no coroutines returns immediately; gather not called."""
        # Arrange / Act / Assert — must not raise and must not call asyncio.gather
        _target = "lyra.bootstrap.lifecycle.lifecycle_helpers.asyncio.gather"
        with patch(_target) as mock_gather:
            await close_safely("no-op-label")
            mock_gather.assert_not_called()

    async def test_successful_coros_are_all_awaited(self) -> None:
        """close_safely awaits every coroutine when none raises."""
        # Arrange
        awaited: list[str] = []

        async def coro_a() -> None:
            awaited.append("a")

        async def coro_b() -> None:
            awaited.append("b")

        # Act
        await close_safely("success-label", coro_a(), coro_b())

        # Assert
        assert "a" in awaited
        assert "b" in awaited

    async def test_second_coro_awaited_even_when_first_raises(self) -> None:
        """close_safely awaits coro[1] even when coro[0] raises — the core fix."""
        # Arrange
        awaited: list[str] = []

        async def failing_coro() -> None:
            raise RuntimeError("adapter close exploded")

        async def succeeding_coro() -> None:
            awaited.append("second")

        # Act — must not raise
        await close_safely("isolation-label", failing_coro(), succeeding_coro())

        # Assert — second coro was still reached
        assert "second" in awaited

    async def test_non_cancelled_exception_is_logged_with_label(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A RuntimeError from a coro is logged and includes the label."""

        # Arrange
        async def bad_coro() -> None:
            raise RuntimeError("boom")

        # Act
        import logging

        with caplog.at_level(
            logging.ERROR, logger="lyra.bootstrap.lifecycle.lifecycle_helpers"
        ):
            await close_safely("tg-adapters", bad_coro())

        # Assert
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert "tg-adapters" in record.getMessage()
        assert "Close failed" in record.getMessage()

    async def test_cancelled_error_logged_at_debug_not_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CancelledError is logged at DEBUG only — not as error."""

        # Arrange
        async def cancelled_coro() -> None:
            raise asyncio.CancelledError()

        import logging

        # Act — capture at DEBUG so we see the debug record
        with caplog.at_level(
            logging.DEBUG, logger="lyra.bootstrap.lifecycle.lifecycle_helpers"
        ):
            await close_safely("shutdown-label", cancelled_coro())

        # Assert — one DEBUG record, no ERROR record
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.DEBUG
        assert "shutdown-label" in caplog.records[0].getMessage()
        # Negative: not logged as exception/error
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records == []

    async def test_multiple_failures_each_logged_independently(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Each failing coro produces its own log record — no short-circuit."""

        # Arrange
        async def bad_a() -> None:
            raise ValueError("error-a")

        async def bad_b() -> None:
            raise TypeError("error-b")

        # Act
        import logging

        with caplog.at_level(
            logging.ERROR, logger="lyra.bootstrap.lifecycle.lifecycle_helpers"
        ):
            await close_safely("multi-fail", bad_a(), bad_b())

        # Assert — one record per failure
        assert len(caplog.records) == 2
        messages = [r.getMessage() for r in caplog.records]
        assert all("multi-fail" in m for m in messages)

    async def test_cancelled_error_mixed_with_real_exception_logs_only_real(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """CancelledError is skipped even when mixed with a real exception."""

        # Arrange
        async def cancelled_coro() -> None:
            raise asyncio.CancelledError()

        async def erroring_coro() -> None:
            raise OSError("disk gone")

        # Act
        import logging

        with caplog.at_level(
            logging.ERROR, logger="lyra.bootstrap.lifecycle.lifecycle_helpers"
        ):
            await close_safely("mixed-label", cancelled_coro(), erroring_coro())

        # Assert — exactly one record (the OSError), not two
        assert len(caplog.records) == 1
        assert "mixed-label" in caplog.records[0].getMessage()
