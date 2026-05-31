"""Unit tests for _emitter_run.py — free functions extracted from OutboundEmitter.

Covers _prepend, _run_event_loop, and _run_emitter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.messaging import (
    RenderEvent,
    RunErrorRenderEvent,
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallResultRenderEvent,
    ToolCallStartRenderEvent,
)
from lyra.core.messaging.message import OutboundMessage
from lyra.outbound._emitter_run import (
    _prepend,
    _run_emitter,
    _run_event_loop,
)
from lyra.outbound.emitter import OutboundEmitter as StreamingSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_formatter(**overrides: Any) -> MagicMock:
    """Build a mock OutboundFormatter with AsyncMock/MagicMock defaults."""
    fmt = MagicMock()
    fmt.placeholder_text = MagicMock(return_value="…")
    fmt.chunk = MagicMock(side_effect=lambda t: [t] if t else [])
    fmt.get_msg = MagicMock(side_effect=lambda key, fallback: fallback)
    fmt.send_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.edit_placeholder_text = AsyncMock()
    fmt.send_trace_placeholder = AsyncMock(return_value=(object(), 42))
    fmt.send_message = AsyncMock(return_value=99)
    fmt.send_fallback = AsyncMock(return_value=77)
    fmt.edit_reasoning = AsyncMock()
    fmt.edit_tool_recap = AsyncMock()
    for k, v in overrides.items():
        setattr(fmt, k, v)
    return fmt


async def _async_iter(*evts: RenderEvent) -> AsyncIterator[RenderEvent]:
    for e in evts:
        yield e


# ---------------------------------------------------------------------------
# _prepend
# ---------------------------------------------------------------------------


class TestPrepend:
    async def test_prepend_yields_first_then_rest(self) -> None:
        """_prepend yields the first item, then all items from the rest iterator."""
        # Arrange
        first = TextStartRenderEvent(message_id="m1")
        rest = _async_iter(
            TextDeltaRenderEvent(message_id="m1", delta="hello"),
            TextEndRenderEvent(message_id="m1"),
        )

        # Act
        items = []
        async for item in _prepend(first, rest):
            items.append(item)

        # Assert
        assert items == [
            TextStartRenderEvent(message_id="m1"),
            TextDeltaRenderEvent(message_id="m1", delta="hello"),
            TextEndRenderEvent(message_id="m1"),
        ]


# ---------------------------------------------------------------------------
# _run_event_loop
# ---------------------------------------------------------------------------


class TestRunEventLoop:
    async def test_routes_text_events_to_on_text_v2(self) -> None:
        """Text* events are routed to _on_text_v2."""
        # Arrange
        seen: list[type] = []

        class _SpySession(StreamingSession):
            async def _on_text_v2(
                self,
                event: TextStartRenderEvent
                | TextDeltaRenderEvent
                | TextEndRenderEvent
                | TextChunkRenderEvent,
                placeholder_obj: object = None,
            ) -> None:
                seen.append(type(event))

        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = _SpySession(fmt, outbound=outbound)
        events = [
            TextStartRenderEvent(message_id="m1"),
            TextDeltaRenderEvent(message_id="m1", delta="x"),
            TextEndRenderEvent(message_id="m1"),
            TextChunkRenderEvent(message_id="m2", delta="y"),
        ]

        # Act
        await _run_event_loop(
            session,
            _async_iter(*events),
            placeholder_obj=object(),
        )

        # Assert
        assert seen == [
            TextStartRenderEvent,
            TextDeltaRenderEvent,
            TextEndRenderEvent,
            TextChunkRenderEvent,
        ]

    async def test_routes_tool_events_to_on_toolcall_v2(self) -> None:
        """ToolCall* events are routed to _on_toolcall_v2."""
        seen: list[type] = []

        class _SpySession(StreamingSession):
            async def _on_toolcall_v2(
                self,
                event: ToolCallStartRenderEvent
                | ToolCallArgsRenderEvent
                | ToolCallEndRenderEvent
                | ToolCallResultRenderEvent,
            ) -> None:
                seen.append(type(event))

        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = _SpySession(fmt, outbound=outbound)
        events = [
            ToolCallStartRenderEvent(tool_call_id="tc1", tool_name="bash"),
            ToolCallArgsRenderEvent(tool_call_id="tc1", delta='{"command":"ls"}'),
            ToolCallEndRenderEvent(tool_call_id="tc1"),
            ToolCallResultRenderEvent(tool_call_id="tc1", content="ok"),
        ]

        await _run_event_loop(
            session,
            _async_iter(*events),
            placeholder_obj=object(),
        )

        assert seen == [
            ToolCallStartRenderEvent,
            ToolCallArgsRenderEvent,
            ToolCallEndRenderEvent,
            ToolCallResultRenderEvent,
        ]

    async def test_routes_run_lifecycle_events_as_no_op(self) -> None:
        """RunStarted/RunFinished are absorbed; RunError sets is_error_pending."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        events = [
            RunStartedRenderEvent(run_id="r1"),
            RunErrorRenderEvent(run_id="r1", message="boom"),
            RunFinishedRenderEvent(run_id="r1"),
        ]

        await _run_event_loop(
            session,
            _async_iter(*events),
            placeholder_obj=object(),
        )

        # RunError sets the flag
        assert session._st.is_error_pending is True

    async def test_routes_reasoning_events_to_edit_reasoning(self) -> None:
        """Reasoning* events are routed through fmt.edit_reasoning."""
        from lyra.core.messaging import (
            ReasoningDeltaRenderEvent,
            ReasoningEndRenderEvent,
            ReasoningStartRenderEvent,
        )

        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        events = [
            ReasoningStartRenderEvent(message_id="r1"),
            ReasoningDeltaRenderEvent(message_id="r1", delta="thinking"),
            ReasoningEndRenderEvent(message_id="r1"),
        ]

        await _run_event_loop(
            session,
            _async_iter(*events),
            placeholder_obj=object(),
        )

        # ReasoningStart triggers _ensure_trace_obj which calls send_trace_placeholder
        fmt.send_trace_placeholder.assert_awaited()
        # edit_reasoning called for each reasoning event
        assert fmt.edit_reasoning.await_count == 3

    async def test_assert_never_on_unknown_event_type(self) -> None:
        """An unknown event type hits assert_never; the AssertionError is caught
        by the broad terminal exception handler and stored in stream_error."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        class UnknownEvent:
            pass

        await _run_event_loop(
            session,
            _async_iter(UnknownEvent()),  # type: ignore[list-item]
            placeholder_obj=object(),
        )

        # assert_never raises AssertionError, which is caught by the broad
        # except Exception in _run_event_loop and stored in stream_error.
        assert isinstance(session._st.stream_error, AssertionError)

    async def test_captures_stream_error_in_st(self) -> None:
        """An exception during event iteration is stored in stream_error."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        async def _broken() -> AsyncIterator[RenderEvent]:
            yield TextStartRenderEvent(message_id="m1")
            raise RuntimeError("stream break")

        await _run_event_loop(
            session,
            _broken(),
            placeholder_obj=object(),
        )

        assert isinstance(session._st.stream_error, RuntimeError)
        assert str(session._st.stream_error) == "stream break"


# ---------------------------------------------------------------------------
# _run_emitter
# ---------------------------------------------------------------------------


class TestRunEmitter:
    async def test_empty_stream_drains_fallback_and_typing_tail(self) -> None:
        """Empty stream: no placeholder, drain fallback + typing tail."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        async def _empty() -> AsyncIterator[RenderEvent]:
            if False:
                yield  # makes this an async generator
            return

        await _run_emitter(session, _empty())

        # Negative: if _drain_fallback branch were removed, send_fallback
        # would not be called with the placeholder text.
        fmt.send_fallback.assert_awaited_once_with("…")

    async def test_peek_error_sets_stream_error_and_re_raises(self) -> None:
        """Peek error: stream_error set, placeholder sent, then re-raised."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        async def _raises_on_peek() -> AsyncIterator[RenderEvent]:
            if False:
                yield  # makes this an async generator
            raise RuntimeError("peek boom")

        with pytest.raises(RuntimeError, match="peek boom"):
            await _run_emitter(session, _raises_on_peek())

        assert isinstance(session._st.stream_error, RuntimeError)
        assert str(session._st.stream_error) == "peek boom"
        fmt.send_placeholder.assert_awaited()
        fmt.edit_placeholder_text.assert_awaited()

    async def test_normal_stream_placeholder_event_loop_final_typing(self) -> None:
        """Normal stream: placeholder → event loop → final → typing tail."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        events = [
            TextStartRenderEvent(message_id="m1"),
            TextDeltaRenderEvent(message_id="m1", delta="hello"),
            TextEndRenderEvent(message_id="m1"),
        ]

        await _run_emitter(session, _async_iter(*events))

        fmt.send_placeholder.assert_awaited()
        fmt.edit_placeholder_text.assert_awaited()
        fmt.send_message.assert_not_awaited()

    async def test_placeholder_failure_calls_drain_fallback(self) -> None:
        """Placeholder send fails: fall back to drain + typing tail."""
        fmt = _make_formatter(send_placeholder=AsyncMock(return_value=(object(), 42)))

        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        # Force _send_placeholder to return None by making guard return Err
        async def _failing_guard(call, *, context):
            from lyra.transport._result import Err, SanitizedError

            if context == "send_placeholder":
                return Err(SanitizedError(code="test", message="Boom", retryable=False))
            # Let other calls through
            from lyra.transport._result import Ok

            return Ok(await call())

        session._handler.guard = _failing_guard  # type: ignore[method-assign]

        events = [
            TextStartRenderEvent(message_id="m1"),
            TextDeltaRenderEvent(message_id="m1", delta="hello"),
            TextEndRenderEvent(message_id="m1"),
        ]

        await _run_emitter(session, _async_iter(*events))

        fmt.send_fallback.assert_awaited_once_with("hello")

    async def test_async_generator_closed_in_finally(self) -> None:
        """AsyncGenerator is aclosed in the finally block even after an exception."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        aclose_tracker: list[bool] = []

        async def _gen() -> AsyncIterator[RenderEvent]:
            try:
                yield TextStartRenderEvent(message_id="m1")
            except GeneratorExit:
                aclose_tracker.append(True)
                raise

        # Patch _send_placeholder to raise after the peek so the generator is
        # still alive when the finally block runs.
        async def _failing_send_placeholder(*args, **kwargs):
            raise RuntimeError("placeholder boom")

        with patch(
            "lyra.outbound._emitter_run._send_placeholder",
            side_effect=_failing_send_placeholder,
        ):
            with pytest.raises(RuntimeError, match="placeholder boom"):
                await _run_emitter(session, _gen())

        assert aclose_tracker == [True]
