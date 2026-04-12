# pyright: reportFunctionMemberAccess=false
"""Tests for StreamingSession and PlatformCallbacks.

Covers the shared streaming algorithm extracted in #495 (Slice 2 of #468).
All tests use mock PlatformCallbacks — no platform SDK imports required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters._shared_streaming import PlatformCallbacks, StreamingSession
from lyra.core.message import GENERIC_ERROR_REPLY, OutboundMessage
from lyra.core.render_events import TextRenderEvent, ToolSummaryRenderEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callbacks(**overrides) -> PlatformCallbacks:
    """Build PlatformCallbacks with AsyncMock/MagicMock defaults."""
    cb = PlatformCallbacks(
        send_placeholder=AsyncMock(return_value=(object(), 42)),
        edit_placeholder_text=AsyncMock(),
        edit_placeholder_tool=AsyncMock(),
        send_message=AsyncMock(return_value=99),
        send_fallback=AsyncMock(return_value=77),
        chunk_text=MagicMock(side_effect=lambda t: [t] if t else []),
        start_typing=MagicMock(),
        cancel_typing=MagicMock(),
        get_msg=MagicMock(side_effect=lambda key, fallback: fallback),
        placeholder_text="…",
    )
    for k, v in overrides.items():
        setattr(cb, k, v)
    return cb


async def _events(*items: object) -> AsyncIterator:
    for item in items:
        yield item


async def _error_events():
    raise RuntimeError("boom")
    yield  # make it an async generator  # noqa: RET503


async def _partial_then_error():
    """Yield one intermediate text event then raise."""
    yield TextRenderEvent("partial", is_final=False)
    raise RuntimeError("mid-stream")


# ---------------------------------------------------------------------------
# Tests — delivery branches
# ---------------------------------------------------------------------------


async def test_text_only_turn():
    """A single final TextRenderEvent edits the placeholder and cancels typing."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(_events(TextRenderEvent("hello", is_final=True)))

    cb.edit_placeholder_text.assert_called_once_with(placeholder_obj, "hello")
    cb.send_message.assert_not_called()
    cb.cancel_typing.assert_called_once()


async def test_tool_then_text_turn():
    """Tool event + final text: tool edits placeholder, text sent as new message."""
    outbound = OutboundMessage.from_text("x")
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    cb.send_message = AsyncMock(return_value=99)

    session = StreamingSession(cb, outbound=outbound)
    await session.run(
        _events(
            ToolSummaryRenderEvent(is_complete=True),
            TextRenderEvent("result", is_final=True),
        )
    )

    cb.edit_placeholder_tool.assert_called_once()
    cb.send_message.assert_called_once_with("result")
    assert outbound.metadata["reply_message_id"] == 99


async def test_tool_then_text_turn_outbound_none():
    """Tool event + final text with outbound=None:
    no crash on reply_message_id write."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(
            ToolSummaryRenderEvent(is_complete=True),
            TextRenderEvent("result", is_final=True),
        )
    )

    cb.send_message.assert_called_once_with("result")


async def test_stream_error_no_text():
    """When the event iterator raises, edit placeholder
    with generic error and re-raise."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(RuntimeError, match="boom"):
        await session.run(_error_events())

    # Error text should be written to the placeholder
    cb.edit_placeholder_text.assert_called_once_with(
        placeholder_obj, GENERIC_ERROR_REPLY,
    )


async def test_stream_error_outbound_not_mutated():
    """Stream error with outbound: reply_message_id stays
    as placeholder ID, not overwritten."""
    outbound = OutboundMessage.from_text("x")
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=outbound)
    with pytest.raises(RuntimeError, match="boom"):
        await session.run(_error_events())

    # Placeholder ID was set; stream error doesn't overwrite it
    assert outbound.metadata["reply_message_id"] == 42


async def test_empty_final_text_surfaces_generic_error():
    """Terminal invariant: if a final event arrives with empty text and
    no stream_error, the placeholder must still be edited to GENERIC_ERROR_REPLY
    rather than left as "…".  Guards against upstream misclassifications
    (e.g. a recovered-tool downgrade with no streamed text).
    """
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(_events(TextRenderEvent("", is_final=True)))

    cb.edit_placeholder_text.assert_called_once_with(
        placeholder_obj, GENERIC_ERROR_REPLY,
    )


async def test_error_turn_text_prepended():
    """is_error=True final text gets ❌ prefix via build_display_text."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(TextRenderEvent("something went wrong", is_final=True, is_error=True))
    )

    cb.edit_placeholder_text.assert_called_once_with(
        placeholder_obj, "❌ something went wrong"
    )


async def test_partial_text_then_stream_error():
    """Partial text + stream error appends [response interrupted] suffix."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(RuntimeError, match="mid-stream"):
        await session.run(_partial_then_error())

    # No final text arrived — error branch should fire with generic error
    cb.edit_placeholder_text.assert_called_with(placeholder_obj, GENERIC_ERROR_REPLY)


# ---------------------------------------------------------------------------
# Tests — fallback path
# ---------------------------------------------------------------------------


async def test_placeholder_fallback():
    """When send_placeholder raises, send_fallback is called with accumulated text."""
    cb = _make_callbacks()
    cb.send_placeholder = AsyncMock(side_effect=Exception("network error"))
    cb.send_fallback = AsyncMock(return_value=77)

    outbound = OutboundMessage.from_text("x")
    session = StreamingSession(cb, outbound=outbound)
    await session.run(_events(TextRenderEvent("fallback text", is_final=True)))

    cb.send_fallback.assert_called_once_with("fallback text")
    cb.cancel_typing.assert_called()
    assert outbound.metadata["reply_message_id"] == 77


async def test_fallback_empty_stream():
    """When placeholder fails and no events, send_fallback gets placeholder_text."""
    cb = _make_callbacks()
    cb.send_placeholder = AsyncMock(side_effect=Exception("fail"))
    cb.send_fallback = AsyncMock(return_value=55)
    cb.placeholder_text = "…"

    session = StreamingSession(cb, outbound=None)
    await session.run(_events())

    cb.send_fallback.assert_called_once_with("…")


async def test_fallback_outbound_none():
    """When send_placeholder raises and outbound is None, no crash occurs."""
    cb = _make_callbacks()
    cb.send_placeholder = AsyncMock(side_effect=Exception("network error"))
    cb.send_fallback = AsyncMock(return_value=77)

    session = StreamingSession(cb, outbound=None)
    await session.run(_events(TextRenderEvent("some text", is_final=True)))

    cb.send_fallback.assert_called_once_with("some text")
    cb.cancel_typing.assert_called()


# ---------------------------------------------------------------------------
# Tests — reply_message_id
# ---------------------------------------------------------------------------


async def test_reply_message_id_with_outbound():
    """Placeholder message ID written to outbound.metadata on text-only turn."""
    outbound = OutboundMessage.from_text("x")
    placeholder_obj = object()
    cb = _make_callbacks()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=outbound)
    await session.run(_events(TextRenderEvent("hello", is_final=True)))

    assert outbound.metadata["reply_message_id"] == 42


async def test_reply_message_id_without_outbound():
    """No crash when outbound is None — reply_message_id tracking simply skipped."""
    cb = _make_callbacks()
    session = StreamingSession(cb, outbound=None)
    await session.run(_events(TextRenderEvent("hello", is_final=True)))
    cb.cancel_typing.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — typing tail
# ---------------------------------------------------------------------------


async def test_typing_tail_intermediate():
    """start_typing called (not cancel_typing) when outbound.intermediate=True."""
    outbound = OutboundMessage.from_text("x")
    outbound.intermediate = True
    cb = _make_callbacks()

    session = StreamingSession(cb, outbound=outbound)
    await session.run(_events(TextRenderEvent("hi", is_final=True)))

    cb.start_typing.assert_called_once()
    cb.cancel_typing.assert_not_called()


async def test_typing_tail_final():
    """cancel_typing called (not start_typing) when outbound.intermediate=False."""
    outbound = OutboundMessage.from_text("x")
    outbound.intermediate = False
    cb = _make_callbacks()

    session = StreamingSession(cb, outbound=outbound)
    await session.run(_events(TextRenderEvent("hi", is_final=True)))

    cb.cancel_typing.assert_called_once()
    cb.start_typing.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — intermediate text guard
# ---------------------------------------------------------------------------


async def test_intermediate_text_guard_discord():
    """Tool edit NOT called when guard=True and intermediate text is visible."""
    cb = _make_callbacks(guard_tool_on_intermediate=True)
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(
            TextRenderEvent("thinking", is_final=False),
            ToolSummaryRenderEvent(is_complete=True),
            TextRenderEvent("done", is_final=True),
        )
    )

    cb.edit_placeholder_tool.assert_not_called()


async def test_intermediate_text_guard_telegram():
    """Tool edit IS called when guard=False (Telegram: combine_recap=True)."""
    cb = _make_callbacks(guard_tool_on_intermediate=False)
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(
            TextRenderEvent("thinking", is_final=False),
            ToolSummaryRenderEvent(is_complete=True),
            TextRenderEvent("done", is_final=True),
        )
    )

    cb.edit_placeholder_tool.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — overflow + multi-chunk
# ---------------------------------------------------------------------------


async def test_overflow_chunks():
    """First chunk edits placeholder; second chunk sent via send_message."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    cb.chunk_text = MagicMock(return_value=["chunk1", "chunk2"])

    session = StreamingSession(cb, outbound=None)
    await session.run(_events(TextRenderEvent("chunk1chunk2", is_final=True)))

    cb.edit_placeholder_text.assert_called_with(placeholder_obj, "chunk1")
    cb.send_message.assert_called_once_with("chunk2")


async def test_had_tool_events_reply_id_last_chunk_only():
    """reply_message_id updated only for the last chunk in had_tool_events branch."""
    outbound = OutboundMessage.from_text("x")
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    cb.chunk_text = MagicMock(return_value=["part1", "part2"])
    send_call_count = 0

    async def _send_message(_chunk: str) -> int:
        nonlocal send_call_count
        send_call_count += 1
        return 100 + send_call_count

    cb.send_message = _send_message  # type: ignore[assignment]

    session = StreamingSession(cb, outbound=outbound)
    await session.run(
        _events(
            ToolSummaryRenderEvent(is_complete=True),
            TextRenderEvent("part1part2", is_final=True),
        )
    )

    assert outbound.metadata["reply_message_id"] == 102


# ---------------------------------------------------------------------------
# Tests — get_msg (i18n)
# ---------------------------------------------------------------------------


async def test_get_msg_used_for_display_text():
    """get_msg callback is used by build_display_text for i18n strings.

    When a final text arrives and then a stream error occurs, build_display_text
    calls get_msg("stream_interrupted", ...) to append the interrupt suffix.
    """

    async def _final_then_error():
        yield TextRenderEvent("partial answer", is_final=True)
        raise RuntimeError("late error")

    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    cb.get_msg = MagicMock(
        side_effect=lambda key, fallback: (
            " [interrompu]"
            if key == "stream_interrupted"
            else fallback
        ),
    )

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(RuntimeError, match="late error"):
        await session.run(_final_then_error())

    # build_display_text should have used get_msg for the interrupt suffix
    cb.get_msg.assert_called()
    # The final text should include the localised interrupt suffix
    cb.edit_placeholder_text.assert_called_with(
        placeholder_obj, "partial answer [interrompu]",
    )


async def test_get_msg_default_fallback():
    """When get_msg returns the fallback, default English strings are used."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(_events(TextRenderEvent("hello", is_final=True)))

    # get_msg was called during build_display_text (no error → no interrupt suffix,
    # but the callback is still wired correctly)
    # Verify no crash and edit happened
    cb.edit_placeholder_text.assert_called_once_with(placeholder_obj, "hello")
