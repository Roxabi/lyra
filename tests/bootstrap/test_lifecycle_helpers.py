"""Tests for shared lifecycle helpers."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.bootstrap.lifecycle.lifecycle_helpers import (
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
