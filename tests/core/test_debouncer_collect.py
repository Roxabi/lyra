"""Tests for MessageDebouncer.collect() — async debounce window behaviour (issue #145).
"""

from __future__ import annotations

import asyncio

import pytest

from lyra.core.debouncer import MessageDebouncer
from lyra.core.message import InboundMessage
from tests.core.conftest import make_debouncer_msg

# ---------------------------------------------------------------------------
# MessageDebouncer.collect() — async collection tests
# ---------------------------------------------------------------------------


class TestCollect:
    """MessageDebouncer.collect() debounce window behaviour."""

    @pytest.mark.asyncio
    async def test_single_message_returns_after_debounce(self) -> None:
        """A single message is returned after the debounce window expires."""
        debouncer = MessageDebouncer(debounce_ms=50)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        msg = make_debouncer_msg("hello")
        inbox.put_nowait(msg)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=2.0)
        assert result == [msg]

    @pytest.mark.asyncio
    async def test_burst_aggregated(self) -> None:
        """Rapid messages within the debounce window are aggregated."""
        debouncer = MessageDebouncer(debounce_ms=200)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()

        m1 = make_debouncer_msg("first")
        m2 = make_debouncer_msg("second")
        m3 = make_debouncer_msg("third")

        # Pre-fill the queue (all arrive before collect starts).
        inbox.put_nowait(m1)
        inbox.put_nowait(m2)
        inbox.put_nowait(m3)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=2.0)
        assert len(result) == 3
        assert result == [m1, m2, m3]

    @pytest.mark.asyncio
    async def test_zero_debounce_no_wait(self) -> None:
        """debounce_ms=0 drains immediately without waiting."""
        debouncer = MessageDebouncer(debounce_ms=0)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        msg = make_debouncer_msg("fast")
        inbox.put_nowait(msg)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=0.5)
        assert result == [msg]

    @pytest.mark.asyncio
    async def test_zero_debounce_drains_queued(self) -> None:
        """debounce_ms=0 still drains whatever is already on the queue."""
        debouncer = MessageDebouncer(debounce_ms=0)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        m1 = make_debouncer_msg("a")
        m2 = make_debouncer_msg("b")
        inbox.put_nowait(m1)
        inbox.put_nowait(m2)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=0.5)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_delayed_message_outside_window(self) -> None:
        """A message arriving after the debounce window is NOT aggregated."""
        debouncer = MessageDebouncer(debounce_ms=50)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()

        m1 = make_debouncer_msg("first")
        m2 = make_debouncer_msg("second")

        inbox.put_nowait(m1)

        async def _delay_put() -> None:
            await asyncio.sleep(0.15)  # well after 50ms window
            inbox.put_nowait(m2)

        asyncio.create_task(_delay_put())

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=2.0)
        assert len(result) == 1
        assert result[0] is m1
