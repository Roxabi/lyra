"""Tests for OutboundDispatcher: routing, callbacks, retries, reap threshold.

Coverage targets:
- _verify_routing: bot_id mismatch, send/streaming routing via msg.routing
- _dispatch_item: unknown kind, _on_dispatched callback (success + circuit-open)
- Circuit-open debounce: second notification suppressed within 60s window
- Retry loop: transient error retried, non-transient not retried
- Error notification: _try_notify_user called after all retries exhausted
- _worker_loop: _SCOPE_REAP_THRESHOLD triggers _reap_scope_locks
- _on_task_done: scope task exception logged, does not crash the dispatcher
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from lyra.core.circuit_breaker import CircuitBreaker
from lyra.core.hub.outbound_dispatcher import OutboundDispatcher
from lyra.core.hub.outbound_errors import _SCOPE_REAP_THRESHOLD
from lyra.core.message import InboundMessage, OutboundMessage, RoutingContext
from lyra.core.render_events import TextRenderEvent

from .conftest import make_dispatcher_msg


def _make_msg_with_routing(routing: RoutingContext) -> InboundMessage:
    """Clone make_dispatcher_msg and inject a RoutingContext into it."""
    msg = make_dispatcher_msg()
    object.__setattr__(msg, "routing", routing)
    return msg


# ---------------------------------------------------------------------------
# _verify_routing — bot_id mismatch
# ---------------------------------------------------------------------------


class TestRoutingBotIdMismatch:
    async def test_bot_id_mismatch_drops_send(self) -> None:
        """Message for wrong bot_id is silently dropped (kind=send)."""
        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            # msg.routing points to "other_bot" — mismatch with dispatcher bot_id="main"
            routing = RoutingContext(
                platform="telegram", bot_id="other_bot", scope_id="chat:123"
            )
            msg = _make_msg_with_routing(routing)
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.05)
            adapter.send.assert_not_awaited()
        finally:
            await dispatcher.stop()

    async def test_bot_id_mismatch_drops_streaming_and_drains(self) -> None:
        """Streaming iterator is drained when bot_id routing mismatches."""
        adapter = MagicMock()
        adapter.send_streaming = AsyncMock()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, bot_id="main"
        )
        await dispatcher.start()
        try:
            routing = RoutingContext(
                platform="telegram", bot_id="other_bot", scope_id="chat:123"
            )
            msg = _make_msg_with_routing(routing)
            drained = False

            async def chunks() -> AsyncIterator[TextRenderEvent]:
                nonlocal drained
                yield TextRenderEvent(text="chunk1", is_final=True)
                drained = True

            dispatcher.enqueue_streaming(msg, chunks())
            await asyncio.sleep(0.05)
            adapter.send_streaming.assert_not_awaited()
            assert drained
        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# _dispatch_item — unknown kind
# ---------------------------------------------------------------------------


async def test_unknown_kind_skipped() -> None:
    """An unknown queue item kind is logged and skipped — no send, no crash."""
    adapter = MagicMock()
    adapter.send = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    await dispatcher.start()
    try:
        msg = make_dispatcher_msg()
        # Inject an unknown kind directly into the queue
        dispatcher._queue.put_nowait(("unknown_kind", msg, None))
        await asyncio.sleep(0.05)
        adapter.send.assert_not_awaited()
        assert dispatcher.qsize() == 0  # item was consumed
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# _on_dispatched callback
# ---------------------------------------------------------------------------


class TestOnDispatchedCallback:
    async def test_callback_called_on_successful_send(self) -> None:
        """_on_dispatched metadata callback is called after a successful send."""
        adapter = MagicMock()
        adapter.send = AsyncMock()
        dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            out = OutboundMessage.from_text("hi")
            called: list[OutboundMessage] = []
            out.metadata["_on_dispatched"] = called.append

            dispatcher.enqueue(msg, out)
            await asyncio.sleep(0.05)

            assert called == [out]
        finally:
            await dispatcher.stop()

    async def test_callback_called_on_circuit_open(self) -> None:
        """_on_dispatched callback is called even when circuit is open.

        reply_message_id is set to None before the callback fires.
        """
        adapter = MagicMock()
        cb = CircuitBreaker(name="telegram", failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        dispatcher = OutboundDispatcher(
            platform_name="telegram", adapter=adapter, circuit=cb
        )
        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            out = OutboundMessage.from_text("hi")
            called: list[OutboundMessage] = []
            out.metadata["_on_dispatched"] = called.append

            dispatcher.enqueue(msg, out)
            await asyncio.sleep(0.05)

            assert called == [out]
            assert out.metadata.get("reply_message_id") is None
        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# Circuit-open debounce
# ---------------------------------------------------------------------------


async def test_circuit_open_debounce_suppresses_second_notification() -> None:
    """Circuit-open user notification sent only once per debounce window per scope."""
    adapter = MagicMock()
    adapter.send = AsyncMock()
    cb = CircuitBreaker(name="telegram", failure_threshold=1)
    cb.record_failure()
    assert cb.is_open()

    dispatcher = OutboundDispatcher(
        platform_name="telegram", adapter=adapter, circuit=cb
    )
    await dispatcher.start()
    try:
        msg = make_dispatcher_msg()  # scope_id = "chat:123"
        notify_calls: list[str] = []

        async def fake_notify(*args: object, **kwargs: object) -> None:
            notify_calls.append(str(args[3]))

        _patch = patch(
            "lyra.core.hub.outbound_dispatcher.try_notify_user", side_effect=fake_notify
        )
        with _patch:
            dispatcher.enqueue(msg, OutboundMessage.from_text("first"))
            dispatcher.enqueue(msg, OutboundMessage.from_text("second"))
            await asyncio.sleep(0.1)

        # Two messages dropped but only one notification sent (debounce)
        assert len(notify_calls) == 1
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# Retry loop — transient vs non-transient errors
# ---------------------------------------------------------------------------


async def test_transient_error_retried_and_succeeds() -> None:
    """Transient error (ConnectionError) triggers a retry; second attempt succeeds."""
    adapter = MagicMock()
    call_count = 0
    done_event = asyncio.Event()

    async def flaky_send(_msg: InboundMessage, _out: OutboundMessage) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("network timeout")
        done_event.set()

    adapter.send = AsyncMock(side_effect=flaky_send)
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)

    # Patch asyncio.sleep so backoff delays are instant (>= 0.5s → no-op)
    original_sleep = asyncio.sleep

    async def fast_sleep(delay: float) -> None:
        if delay < 0.5:
            await original_sleep(delay)

    await dispatcher.start()
    try:
        msg = make_dispatcher_msg()
        with patch("asyncio.sleep", new=fast_sleep):
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.wait_for(done_event.wait(), timeout=2.0)

        assert call_count == 2
    finally:
        await dispatcher.stop()


async def test_non_transient_error_not_retried() -> None:
    """Non-transient error (ValueError) is not retried — adapter called exactly once."""
    adapter = MagicMock()
    call_count = 0

    async def bad_send(_msg: InboundMessage, _out: OutboundMessage) -> None:
        nonlocal call_count
        call_count += 1
        raise ValueError("permanent error")

    adapter.send = AsyncMock(side_effect=bad_send)
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    await dispatcher.start()
    try:
        msg = make_dispatcher_msg()
        dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
        await asyncio.sleep(0.1)
        assert call_count == 1
    finally:
        await dispatcher.stop()


async def test_failed_send_notifies_user() -> None:
    """After permanent send failure, _try_notify_user is called once."""
    adapter = MagicMock()
    adapter.send = AsyncMock(side_effect=ValueError("permanent"))
    notify_calls: list[str] = []

    async def fake_notify(*args: object, **kwargs: object) -> None:
        notify_calls.append(str(args[3]))

    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    await dispatcher.start()
    try:
        msg = make_dispatcher_msg()
        _patch = patch(
            "lyra.core.hub.outbound_dispatcher.try_notify_user", side_effect=fake_notify
        )
        with _patch:
            dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
            await asyncio.sleep(0.1)
        assert len(notify_calls) == 1
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# _worker_loop — _SCOPE_REAP_THRESHOLD triggers _reap_scope_locks
# ---------------------------------------------------------------------------


async def test_scope_reap_triggered_at_threshold() -> None:
    """When _scope_locks exceeds _SCOPE_REAP_THRESHOLD, idle locks are reaped."""
    adapter = MagicMock()
    adapter.send = AsyncMock()
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    await dispatcher.start()
    try:
        # Pre-populate _scope_locks with idle locks beyond the threshold
        for i in range(_SCOPE_REAP_THRESHOLD + 1):
            dispatcher._scope_locks[f"scope:{i}"] = asyncio.Lock()

        # Enqueue one message — _worker_loop sees len > threshold → triggers reap
        msg = make_dispatcher_msg()
        dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
        await asyncio.sleep(0.05)

        # All pre-populated locks were idle at reap time → reaped
        # The msg's lock (chat:123) may also be idle by now → also reaped
        assert len(dispatcher._scope_locks) <= 1
    finally:
        await dispatcher.stop()


# ---------------------------------------------------------------------------
# Retry exhaustion — transient error on every attempt
# ---------------------------------------------------------------------------


class TestRetryExhaustion:
    async def test_transient_error_exhausts_retries_and_notifies(self) -> None:
        """ConnectionError on every attempt: 4 sends, 1 notify, 1 circuit failure."""
        # Arrange
        adapter = MagicMock()
        done_event = asyncio.Event()

        async def always_fail(_msg: InboundMessage, _out: OutboundMessage) -> None:
            raise ConnectionError("network unreachable")

        adapter.send = AsyncMock(side_effect=always_fail)

        mock_circuit = MagicMock(spec=CircuitBreaker)
        mock_circuit.is_open.return_value = False

        dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=adapter,
            circuit=mock_circuit,
        )

        notify_mock = AsyncMock(side_effect=lambda *_, **__: done_event.set())

        await dispatcher.start()
        try:
            msg = make_dispatcher_msg()
            with (
                patch("asyncio.sleep", new=AsyncMock()),
                patch(
                    "lyra.core.hub.outbound_dispatcher.try_notify_user",
                    new=notify_mock,
                ),
            ):
                dispatcher.enqueue(msg, OutboundMessage.from_text("hi"))
                # Act — wait until try_notify_user fires (exhaustion complete)
                await asyncio.wait_for(done_event.wait(), timeout=2.0)

            # Assert
            assert adapter.send.await_count == 4  # 1 initial + 3 retries
            notify_mock.assert_awaited_once()
            mock_circuit.record_failure.assert_called_once()
        finally:
            await dispatcher.stop()


# ---------------------------------------------------------------------------
# _on_task_done — scope task exception logged, dispatcher keeps running
# ---------------------------------------------------------------------------


async def test_scope_task_exception_logged_and_dispatcher_survives() -> None:
    """If a scope task raises, it's logged and the dispatcher keeps running."""
    adapter = MagicMock()
    call_count = 0

    async def boom_then_ok(_msg: InboundMessage, _out: OutboundMessage) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("unexpected scope error")

    adapter.send = AsyncMock(side_effect=boom_then_ok)
    dispatcher = OutboundDispatcher(platform_name="telegram", adapter=adapter)
    await dispatcher.start()
    try:
        msg = make_dispatcher_msg()
        dispatcher.enqueue(msg, OutboundMessage.from_text("first"))
        await asyncio.sleep(0.05)
        # Dispatcher still alive — second message dispatched normally
        dispatcher.enqueue(msg, OutboundMessage.from_text("second"))
        await asyncio.sleep(0.05)
        assert call_count == 2
    finally:
        await dispatcher.stop()
