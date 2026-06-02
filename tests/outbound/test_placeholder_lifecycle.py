"""Unit tests for _placeholder_lifecycle.py.

Covers _send_placeholder, _drain_fallback, _deliver_text_chunks, _deliver_final,
and _handle_typing_tail.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from factory.core.messaging import (
    RenderEvent,
    TextDeltaRenderEvent,
    ToolCallArgsRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallStartRenderEvent,
)
from factory.core.messaging.message import OutboundMessage
from factory.outbound._placeholder_lifecycle import (
    _deliver_final,
    _deliver_text_chunks,
    _drain_fallback,
    _handle_typing_tail,
    _send_placeholder,
)
from factory.outbound.emitter import OutboundEmitter as StreamingSession
from factory.transport._result import Err, Ok, SanitizedError

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
# _send_placeholder
# ---------------------------------------------------------------------------


class TestSendPlaceholder:
    async def test_success_returns_tuple_and_sets_reply_message_id(self) -> None:
        """Success: returns tuple and writes reply_message_id to metadata."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        ph_obj = object()
        fmt.send_placeholder = AsyncMock(return_value=(ph_obj, 123))

        result = await _send_placeholder(session)

        assert result is not None
        assert result[0] is ph_obj
        assert result[1] == 123
        assert outbound.metadata["reply_message_id"] == 123

    async def test_failure_returns_none_and_cancels_typing(self) -> None:
        """Failure path: returns None and cancels typing indicator."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        async def _failing_guard(call, *, context):
            return Err(SanitizedError(code="test", message="Boom", retryable=False))

        session._handler.guard = _failing_guard  # type: ignore[method-assign]
        session._cancel_typing = AsyncMock()  # type: ignore[method-assign]

        result = await _send_placeholder(session)

        assert result is None
        session._cancel_typing.assert_awaited_once()


# ---------------------------------------------------------------------------
# _drain_fallback
# ---------------------------------------------------------------------------


class TestDrainFallback:
    async def test_accumulates_text_delta_and_sends_fallback(self) -> None:
        """TextDeltaRenderEvent deltas are accumulated and sent via send_fallback."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        await _drain_fallback(
            session,
            _async_iter(
                TextDeltaRenderEvent(message_id="m1", delta="Hello"),
                TextDeltaRenderEvent(message_id="m1", delta=" world"),
            ),
        )

        fmt.send_fallback.assert_awaited_once_with("Hello world")

    async def test_empty_stream_sends_placeholder_text(self) -> None:
        """Empty stream: send_fallback receives placeholder_text()."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        async def _empty() -> AsyncIterator[RenderEvent]:
            if False:
                yield
            return

        await _drain_fallback(session, _empty())

        fmt.send_fallback.assert_awaited_once_with("…")


# ---------------------------------------------------------------------------
# _deliver_text_chunks
# ---------------------------------------------------------------------------


class TestDeliverTextChunks:
    async def test_single_chunk_edits_placeholder(self) -> None:
        """Single chunk: edit_placeholder_text called once, send_message not called."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        placeholder_obj = object()

        await _deliver_text_chunks(session, placeholder_obj, ["hello"])

        fmt.edit_placeholder_text.assert_awaited_once_with(placeholder_obj, "hello")
        fmt.send_message.assert_not_awaited()

    async def test_multiple_chunks_edit_then_send_overflow(self) -> None:
        """Multiple chunks: first chunk edits, remainder sent as new messages."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        placeholder_obj = object()

        await _deliver_text_chunks(
            session, placeholder_obj, ["chunk1", "chunk2", "chunk3"]
        )

        fmt.edit_placeholder_text.assert_awaited_once_with(placeholder_obj, "chunk1")
        assert fmt.send_message.await_count == 2
        fmt.send_message.assert_any_await("chunk2")
        fmt.send_message.assert_any_await("chunk3")

    async def test_error_on_edit_is_non_fatal(self) -> None:
        """Err on edit is swallowed; overflow chunks still attempted."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)

        async def _guard_that_fails_first(call, *, context):
            if context == "deliver_final_edit":
                return Err(
                    SanitizedError(code="test", message="EditFail", retryable=False)
                )
            return Ok(await call())

        session._handler.guard = _guard_that_fails_first  # type: ignore[method-assign]
        placeholder_obj = object()

        await _deliver_text_chunks(session, placeholder_obj, ["chunk1", "chunk2"])

        fmt.send_message.assert_awaited_once_with("chunk2")


# ---------------------------------------------------------------------------
# _deliver_final
# ---------------------------------------------------------------------------


class TestDeliverFinal:
    async def test_with_display_text_calls_deliver_text_chunks(self) -> None:
        """When display text exists, _deliver_text_chunks is invoked."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        session._st.set_final_text("final hello")
        placeholder_obj = object()

        await _deliver_final(session, placeholder_obj)

        fmt.edit_placeholder_text.assert_awaited_once_with(
            placeholder_obj, "final hello"
        )
        fmt.send_message.assert_not_awaited()

    async def test_no_display_text_calls_edit_with_error_text(self) -> None:
        """No display text: edit_placeholder_text receives a generic error fallback."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        placeholder_obj = object()

        await _deliver_final(session, placeholder_obj)

        fmt.edit_placeholder_text.assert_awaited()
        call_text = fmt.edit_placeholder_text.call_args[0][1]
        assert call_text == "Something went wrong. Please try again."

    async def test_tool_events_with_trace_calls_edit_tool_recap_done(self) -> None:
        """Tool events + trace_obj present: edit_tool_recap with done=True."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        session._st.had_tool_events = True
        session._trace_obj = object()
        session._recap_done_emitted = False
        # Feed a tool so recap lines are non-empty
        session._tool_recap.observe_start(
            ToolCallStartRenderEvent(tool_call_id="tc1", tool_name="bash")
        )
        session._tool_recap.observe_args(
            ToolCallArgsRenderEvent(tool_call_id="tc1", delta='{"command":"ls"}')
        )
        session._tool_recap.observe_end(ToolCallEndRenderEvent(tool_call_id="tc1"))
        placeholder_obj = object()

        await _deliver_final(session, placeholder_obj)

        fmt.edit_tool_recap.assert_awaited()
        assert session._recap_done_emitted is True

    async def test_tool_events_without_trace_skips_recap(self) -> None:
        """Tool events but no trace_obj: recap skipped."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        session = StreamingSession(fmt, outbound=outbound)
        session._st.had_tool_events = True
        session._trace_obj = None
        placeholder_obj = object()

        await _deliver_final(session, placeholder_obj)

        fmt.edit_tool_recap.assert_not_awaited()


# ---------------------------------------------------------------------------
# _handle_typing_tail
# ---------------------------------------------------------------------------


class TestHandleTypingTail:
    async def test_intermediate_calls_start_typing(self) -> None:
        """intermediate=True → _start_typing."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        outbound.intermediate = True
        session = StreamingSession(fmt, outbound=outbound)
        session._start_typing = AsyncMock()  # type: ignore[method-assign]
        session._cancel_typing = AsyncMock()  # type: ignore[method-assign]

        await _handle_typing_tail(session)

        session._start_typing.assert_awaited_once()
        session._cancel_typing.assert_not_awaited()

    async def test_final_calls_cancel_typing(self) -> None:
        """intermediate=False → _cancel_typing."""
        fmt = _make_formatter()
        outbound = OutboundMessage.from_text("hi")
        outbound.intermediate = False
        session = StreamingSession(fmt, outbound=outbound)
        session._start_typing = AsyncMock()  # type: ignore[method-assign]
        session._cancel_typing = AsyncMock()  # type: ignore[method-assign]

        await _handle_typing_tail(session)

        session._cancel_typing.assert_awaited_once()
        session._start_typing.assert_not_awaited()

    async def test_outbound_none_is_no_op(self) -> None:
        """outbound=None: no typing calls are made (typing is None by default)."""
        fmt = _make_formatter()
        session = StreamingSession(fmt, outbound=None)

        # No typing mock attached; real _cancel_typing is a no-op because
        # self._typing is None. The call must not raise.
        await _handle_typing_tail(session)
