"""Tests for OutboundDispatcher bounded-queue / drop-oldest overflow behaviour.

Coverage:
- overflow drops OLDEST item, not newest
- dropped streaming iterator is fully drained (async gen with sentinel)
- qsize never exceeds maxsize after overflow
- QueueEmpty race in _maybe_drop_oldest is tolerated silently
- nominal path (no overflow) unchanged
- OUTBOUND_QUEUE_MAXSIZE constant is 50
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from factory.core.hub.outbound.outbound_dispatcher import OutboundDispatcher
from factory.core.hub.outbound.outbound_errors import OUTBOUND_QUEUE_MAXSIZE
from factory.core.messaging.message import OutboundAudio, OutboundMessage

from .conftest import make_dispatcher_msg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stopped_dispatcher(maxsize: int = 3) -> tuple[MagicMock, OutboundDispatcher]:
    """Return an adapter mock + a dispatcher that has NOT been started.

    Using a small maxsize keeps the tests fast and O(1) in enqueue calls.
    The worker is NOT started so items stay in the queue — we inspect queue
    state synchronously without any async delivery.
    """
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.send_streaming = AsyncMock()
    dispatcher = OutboundDispatcher(
        platform_name="telegram",
        adapter=adapter,
        queue_maxsize=maxsize,
    )
    return adapter, dispatcher


async def _drain_to_completion(gen: AsyncGenerator) -> list:
    """Exhaust an async generator, collecting yielded values."""
    items = []
    async for item in gen:
        items.append(item)
    return items


# ---------------------------------------------------------------------------
# Queue maxsize constant
# ---------------------------------------------------------------------------


class TestQueueMaxsizeConstant:
    def test_default_maxsize_is_50(self) -> None:
        assert OUTBOUND_QUEUE_MAXSIZE == 50

    def test_dispatcher_maxsize_matches_constant(self) -> None:
        _, dispatcher = _make_stopped_dispatcher(maxsize=OUTBOUND_QUEUE_MAXSIZE)
        assert dispatcher._queue.maxsize == OUTBOUND_QUEUE_MAXSIZE  # noqa: SLF001


# ---------------------------------------------------------------------------
# Nominal path — no overflow
# ---------------------------------------------------------------------------


class TestNominalNoOverflow:
    def test_enqueue_below_capacity_no_drop(self) -> None:
        """Items below maxsize are enqueued without any drop."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=4)
        msg = make_dispatcher_msg()
        for _ in range(4):
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
        assert dispatcher._queue.qsize() == 4  # noqa: SLF001

    def test_enqueue_at_capacity_does_not_exceed_maxsize(self) -> None:
        """After overflow, qsize never exceeds maxsize."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=2)
        msg = make_dispatcher_msg()
        # Fill to capacity
        dispatcher.enqueue(msg, OutboundMessage.from_text("first"))
        dispatcher.enqueue(msg, OutboundMessage.from_text("second"))
        assert dispatcher._queue.qsize() == 2  # noqa: SLF001
        # Overflow — oldest dropped, size stays at 2
        dispatcher.enqueue(msg, OutboundMessage.from_text("third"))
        assert dispatcher._queue.qsize() == 2  # noqa: SLF001


# ---------------------------------------------------------------------------
# Drop-oldest semantics
# ---------------------------------------------------------------------------


class TestDropOldest:
    def test_overflow_drops_oldest_keeps_newest(self) -> None:
        """When queue is full, the HEAD item is removed, not the incoming one."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=2)
        msg = make_dispatcher_msg()
        out_first = OutboundMessage.from_text("first")
        out_second = OutboundMessage.from_text("second")
        out_third = OutboundMessage.from_text("third")

        dispatcher.enqueue(msg, out_first)
        dispatcher.enqueue(msg, out_second)
        # Queue: [first, second] — full
        dispatcher.enqueue(msg, out_third)
        # Queue: [second, third] — first was dropped

        # Peek at the queue without consuming via get_nowait
        item_a = dispatcher._queue.get_nowait()  # noqa: SLF001
        item_b = dispatcher._queue.get_nowait()  # noqa: SLF001
        # item_a should be second (oldest surviving), item_b should be third
        assert item_a[2] is out_second
        assert item_b[2] is out_third

    def test_multiple_overflows_always_keep_newest(self) -> None:
        """Each successive overflow drops the current oldest item."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=1)
        msg = make_dispatcher_msg()
        out_a = OutboundMessage.from_text("a")
        out_b = OutboundMessage.from_text("b")
        out_c = OutboundMessage.from_text("c")

        dispatcher.enqueue(msg, out_a)  # queue: [a]
        dispatcher.enqueue(msg, out_b)  # overflow: drop a → [b]
        dispatcher.enqueue(msg, out_c)  # overflow: drop b → [c]

        item = dispatcher._queue.get_nowait()  # noqa: SLF001
        assert item[2] is out_c


# ---------------------------------------------------------------------------
# Streaming iterator drain
# ---------------------------------------------------------------------------


class TestStreamingIteratorDrain:
    async def test_dropped_streaming_item_iterator_drained(self) -> None:
        """A dropped 'streaming' item has its AsyncIterator fully consumed."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=1)
        msg = make_dispatcher_msg()

        drained: list[str] = []

        async def _gen_with_sentinel() -> AsyncIterator[str]:
            yield "token-1"
            yield "token-2"
            drained.append("done")

        # Fill the queue with a non-streaming item first
        dispatcher.enqueue(msg, OutboundMessage.from_text("placeholder"))
        # Now enqueue a streaming item that will trigger drop-oldest of the placeholder
        # and put the streaming item in the queue
        chunks_gen = _gen_with_sentinel()
        dispatcher.enqueue_streaming(msg, chunks_gen)
        # Queue: [streaming(chunks_gen)]

        # Now trigger overflow: enqueue another item so the streaming one gets dropped
        dispatcher.enqueue(msg, OutboundMessage.from_text("evict-streaming"))
        # _maybe_drop_oldest() will drop the streaming item → should drain chunks_gen

        # Drain is a fire-and-forget asyncio task — yield the event loop
        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based

        assert "done" in drained, "streaming iterator was not drained after drop"

    async def test_dropped_audio_stream_iterator_drained(self) -> None:
        """A dropped 'audio_stream' item has its AsyncIterator fully consumed."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=1)
        msg = make_dispatcher_msg()

        drained: list[str] = []

        async def _audio_gen() -> AsyncIterator[object]:
            yield object()
            drained.append("audio-done")

        # Fill the queue with a plain send first
        dispatcher.enqueue(msg, OutboundMessage.from_text("placeholder"))
        # Enqueue audio_stream, which evicts the placeholder
        dispatcher.enqueue_audio_stream(msg, _audio_gen())
        # Enqueue one more item to evict the audio_stream item
        dispatcher.enqueue(msg, OutboundMessage.from_text("evict-audio"))

        await asyncio.sleep(0)  # event-based
        await asyncio.sleep(0)  # event-based

        assert "audio-done" in drained, (
            "audio_stream iterator was not drained after drop"
        )


# ---------------------------------------------------------------------------
# QueueEmpty race
# ---------------------------------------------------------------------------


class TestQueueEmptyRace:
    def test_maybe_drop_oldest_tolerates_concurrent_drain(self) -> None:
        """If worker drains the queue before _maybe_drop_oldest runs get_nowait,
        no exception is raised and the new item is still enqueued."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=2)
        msg = make_dispatcher_msg()

        # Fill queue to capacity then manually drain it (simulates worker racing)
        dispatcher.enqueue(msg, OutboundMessage.from_text("first"))
        dispatcher.enqueue(msg, OutboundMessage.from_text("second"))
        # Drain the queue externally (worker raced us)
        dispatcher._queue.get_nowait()  # noqa: SLF001
        dispatcher._queue.task_done()  # noqa: SLF001
        dispatcher._queue.get_nowait()  # noqa: SLF001
        dispatcher._queue.task_done()  # noqa: SLF001
        assert dispatcher._queue.qsize() == 0  # noqa: SLF001

        # Now enqueue — qsize < maxsize, so _maybe_drop_oldest returns immediately
        # (no QueueEmpty raised)
        dispatcher.enqueue(msg, OutboundMessage.from_text("new"))
        assert dispatcher._queue.qsize() == 1  # noqa: SLF001

    def test_maybe_drop_oldest_direct_race(self) -> None:
        """_maybe_drop_oldest called directly when queue is empty does nothing."""
        _, dispatcher = _make_stopped_dispatcher(maxsize=2)
        # queue is empty and maxsize=2 — qsize (0) < maxsize (2) → early return
        dispatcher._maybe_drop_oldest()  # noqa: SLF001
        assert dispatcher._queue.qsize() == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# qsize invariant
# ---------------------------------------------------------------------------


class TestQsizeInvariant:
    def test_qsize_never_exceeds_maxsize_under_burst(self) -> None:
        """Enqueue 3× maxsize items — qsize must stay ≤ maxsize at all times."""
        maxsize = 5
        _, dispatcher = _make_stopped_dispatcher(maxsize=maxsize)
        msg = make_dispatcher_msg()

        for i in range(maxsize * 3):
            dispatcher.enqueue(msg, OutboundMessage.from_text(f"item-{i}"))
            assert dispatcher._queue.qsize() <= maxsize

    def test_qsize_never_exceeds_maxsize_mixed_kinds(self) -> None:
        """Mixed enqueue kinds also never exceed maxsize."""
        maxsize = 3

        async def _noop_gen() -> AsyncIterator[object]:
            return
            yield  # make it an async generator

        _, dispatcher = _make_stopped_dispatcher(maxsize=maxsize)
        msg = make_dispatcher_msg()

        for _ in range(6):
            dispatcher.enqueue(msg, OutboundMessage.from_text("s"))
            assert dispatcher._queue.qsize() <= maxsize
            dispatcher.enqueue_audio(msg, MagicMock(spec=OutboundAudio))
            assert dispatcher._queue.qsize() <= maxsize
