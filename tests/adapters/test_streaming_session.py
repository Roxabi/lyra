# pyright: reportFunctionMemberAccess=false
"""Tests for StreamingSession (OutboundEmitter) behaviour.

Covers the shared streaming algorithm extracted in #495 (Slice 2 of #468).
All tests use a mock OutboundFormatter — no platform SDK imports required.

Updated in Slice 5 (#1192): all v1 TextRenderEvent / ToolSummaryRenderEvent
usages replaced with v2 TextDeltaRenderEvent / TextEndRenderEvent events.

Migrated in S7 (#1501): PlatformCallbacks dataclass replaced with
OutboundFormatter Protocol; tests now use a MagicMock formatter.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.nats.nats_stream_decoder import decode_stream_events
from lyra.core.exceptions import StreamChunkTimeout
from lyra.core.messaging.message import GENERIC_ERROR_REPLY, OutboundMessage
from lyra.core.messaging.render_events import (
    TextDeltaRenderEvent,
    TextEndRenderEvent,
)
from lyra.outbound.emitter import OutboundEmitter as StreamingSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_formatter(**overrides) -> MagicMock:
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


async def _events(*items: object) -> AsyncIterator:
    for item in items:
        yield item


async def _error_events():
    raise RuntimeError("boom")
    yield  # make it an async generator  # noqa: RET503


async def _partial_then_error():
    """Yield one intermediate text delta then raise."""
    yield TextDeltaRenderEvent(message_id="msg1", delta="partial")
    raise RuntimeError("mid-stream")


# ---------------------------------------------------------------------------
# Tests — delivery branches
# ---------------------------------------------------------------------------


async def test_text_only_turn():
    """TextDelta + TextEnd edits the placeholder and cancels typing.

    v2 streaming emits an intermediate edit on the first delta (live feedback
    via "⏳ " prefix) plus a final edit on the closed buffer; the final edit
    is what the user sees, so assert on call_args (last call), not call count.
    """
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    assert fmt.edit_placeholder_text.call_args == ((placeholder_obj, "hello"),)
    fmt.send_message.assert_not_called()


async def test_stream_error_no_text():
    """When the event iterator raises, edit placeholder with descriptive error."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=None)
    with pytest.raises(RuntimeError, match="boom"):
        await session.run(_error_events())

    # Error text shows exception class name only; the original exception's str()
    # is NOT included (PR #1210 review B3 — prevents leaking hostnames, paths,
    # auth tokens that httpx/aiohttp/NATS exceptions may carry).
    args = fmt.edit_placeholder_text.call_args[0]
    assert args[0] is placeholder_obj
    assert "RuntimeError" in args[1]
    assert "boom" not in args[1]
    assert "Please try again" in args[1]


async def test_stream_error_outbound_not_mutated():
    """Stream error with outbound: reply_message_id stays
    as placeholder ID, not overwritten."""
    outbound = OutboundMessage.from_text("x")
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=outbound)
    with pytest.raises(RuntimeError, match="boom"):
        await session.run(_error_events())

    # Placeholder ID was set; stream error doesn't overwrite it
    assert outbound.metadata["reply_message_id"] == 42


async def test_empty_stream_surfaces_generic_error():
    """Terminal invariant: empty stream → placeholder edited to GENERIC_ERROR_REPLY."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=None)
    # Emit only a TextEnd with no prior delta — no text content
    await session.run(
        _events(
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    fmt.edit_placeholder_text.assert_called_once_with(
        placeholder_obj,
        GENERIC_ERROR_REPLY,
    )


async def test_partial_text_then_stream_error():
    """Partial delta + stream error: no final text → descriptive error message."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=None)
    with pytest.raises(RuntimeError, match="mid-stream"):
        await session.run(_partial_then_error())

    # No TextEnd arrived — descriptive error with exception class name only.
    # The original exception's str() is NOT included (PR #1210 review B3 —
    # prevents leaking hostnames, paths, auth tokens via str(exc)).
    args = fmt.edit_placeholder_text.call_args[0]
    assert args[0] is placeholder_obj
    assert "RuntimeError" in args[1]
    assert "mid-stream" not in args[1]
    assert "Please try again" in args[1]


# ---------------------------------------------------------------------------
# Tests — fallback path
# ---------------------------------------------------------------------------


async def test_placeholder_fallback():
    """When send_placeholder raises, send_fallback is called with accumulated text."""
    fmt = _make_formatter()
    fmt.send_placeholder = AsyncMock(side_effect=Exception("network error"))
    fmt.send_fallback = AsyncMock(return_value=77)

    outbound = OutboundMessage.from_text("x")
    session = StreamingSession(fmt, outbound=outbound)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="fallback text"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    fmt.send_fallback.assert_called_once_with("fallback text")
    assert outbound.metadata["reply_message_id"] == 77


async def test_fallback_empty_stream():
    """When placeholder fails and no events, send_fallback gets placeholder_text."""
    fmt = _make_formatter()
    fmt.send_placeholder = AsyncMock(side_effect=Exception("fail"))
    fmt.send_fallback = AsyncMock(return_value=55)
    fmt.placeholder_text = MagicMock(return_value="…")

    session = StreamingSession(fmt, outbound=None)
    await session.run(_events())

    fmt.send_fallback.assert_called_once_with("…")


async def test_fallback_outbound_none():
    """When send_placeholder raises and outbound is None, no crash occurs."""
    fmt = _make_formatter()
    fmt.send_placeholder = AsyncMock(side_effect=Exception("network error"))
    fmt.send_fallback = AsyncMock(return_value=77)

    session = StreamingSession(fmt, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="some text"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    fmt.send_fallback.assert_called_once_with("some text")


# ---------------------------------------------------------------------------
# Tests — reply_message_id
# ---------------------------------------------------------------------------


async def test_reply_message_id_with_outbound():
    """Placeholder message ID written to outbound.metadata on text-only turn."""
    outbound = OutboundMessage.from_text("x")
    placeholder_obj = object()
    fmt = _make_formatter()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=outbound)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    assert outbound.metadata["reply_message_id"] == 42


async def test_reply_message_id_without_outbound():
    """No crash when outbound is None — reply_message_id tracking simply skipped."""
    fmt = _make_formatter()
    session = StreamingSession(fmt, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )


# ---------------------------------------------------------------------------
# Tests — typing tail (now via ThrottleCapability, not callbacks)
# Note: typing is no longer on the formatter; these tests verify the emitter
# still completes without error when typing=None (default).
# ---------------------------------------------------------------------------


async def test_typing_tail_intermediate():
    """With typing=None, intermediate outbound still completes without crash."""
    outbound = OutboundMessage.from_text("x")
    outbound.intermediate = True
    fmt = _make_formatter()

    session = StreamingSession(fmt, outbound=outbound)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hi"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )
    # No assertion on typing — ThrottleCapability is separate from formatter


async def test_typing_tail_final():
    """With typing=None, final outbound still completes without crash."""
    outbound = OutboundMessage.from_text("x")
    outbound.intermediate = False
    fmt = _make_formatter()

    session = StreamingSession(fmt, outbound=outbound)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hi"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )
    # No assertion on typing — ThrottleCapability is separate from formatter


# ---------------------------------------------------------------------------
# Tests — trace placeholder
# ---------------------------------------------------------------------------


async def test_trace_placeholder_not_sent_on_text_only():
    """send_trace_placeholder NOT called on text-only turns (v2 events)."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="done"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    fmt.send_trace_placeholder.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — overflow + multi-chunk
# ---------------------------------------------------------------------------


async def test_overflow_chunks():
    """First chunk edits placeholder; second chunk sent via send_message."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    fmt.chunk = MagicMock(return_value=["chunk1", "chunk2"])

    session = StreamingSession(fmt, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="chunk1chunk2"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    fmt.edit_placeholder_text.assert_called_with(placeholder_obj, "chunk1")
    fmt.send_message.assert_called_once_with("chunk2")


# ---------------------------------------------------------------------------
# Tests — get_msg (i18n)
# ---------------------------------------------------------------------------


async def test_get_msg_used_for_display_text():
    """get_msg callback is used by build_display_text for i18n strings."""

    async def _final_then_error():
        yield TextDeltaRenderEvent(message_id="msg1", delta="partial answer")
        yield TextEndRenderEvent(message_id="msg1")
        raise RuntimeError("late error")

    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    fmt.get_msg = MagicMock(
        side_effect=lambda key, fallback: (
            " [interrompu]" if key == "stream_interrupted" else fallback
        ),
    )

    session = StreamingSession(fmt, outbound=None)
    with pytest.raises(RuntimeError, match="late error"):
        await session.run(_final_then_error())

    # build_display_text should have used get_msg for the interrupt suffix
    fmt.get_msg.assert_called()
    # The final text should include the localised interrupt suffix
    fmt.edit_placeholder_text.assert_called_with(
        placeholder_obj,
        "partial answer [interrompu]",
    )


async def test_get_msg_default_fallback():
    """When get_msg returns the fallback, default English strings are used."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(fmt, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    # get_msg was called during build_display_text (no error → no interrupt suffix,
    # but the callback is still wired correctly). v2 streaming may emit an
    # intermediate edit before the final one; check that the final call delivered
    # the clean text without the "⏳ " live-feedback prefix.
    assert fmt.edit_placeholder_text.call_args == ((placeholder_obj, "hello"),)


# ---------------------------------------------------------------------------
# Tests — StreamChunkTimeout (decode_stream_events timeout path)
# ---------------------------------------------------------------------------


async def test_decode_stream_events_timeout_raises_stream_chunk_timeout():
    """Chunk queue idle past timeout → StreamChunkTimeout raised (not silent break)."""
    q: asyncio.Queue[dict] = asyncio.Queue()
    gen = decode_stream_events("test-stream-id", q)

    # Patch _CHUNK_TIMEOUT_SECONDS to 0.01s so the test doesn't wait 120s
    import lyra.adapters.nats.nats_stream_decoder as _mod

    original = _mod._CHUNK_TIMEOUT_SECONDS
    _mod._CHUNK_TIMEOUT_SECONDS = 0.01
    try:
        with pytest.raises(StreamChunkTimeout, match="no chunk received"):
            async for _ in gen:
                pass
    finally:
        _mod._CHUNK_TIMEOUT_SECONDS = original


async def test_stream_chunk_timeout_is_subclass_of_timeout_error():
    """StreamChunkTimeout is a TimeoutError subclass for isinstance checks."""
    exc = StreamChunkTimeout("test")
    assert isinstance(exc, TimeoutError)
    assert isinstance(exc, StreamChunkTimeout)


async def test_streaming_session_timeout_shows_timeout_message():
    """StreamChunkTimeout from event iterator → timeout-specific message shown."""
    fmt = _make_formatter()
    placeholder_obj = object()
    fmt.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    async def _timeout_events():
        raise StreamChunkTimeout("no chunk received for 120s (stream_id='test')")
        yield  # make it an async generator  # noqa: RET503

    session = StreamingSession(fmt, outbound=None)
    with pytest.raises(StreamChunkTimeout):
        await session.run(_timeout_events())

    args = fmt.edit_placeholder_text.call_args[0]
    assert args[0] is placeholder_obj
    assert "120 s" in args[1]
