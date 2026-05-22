# pyright: reportFunctionMemberAccess=false
"""Tests for StreamingSession and PlatformCallbacks.

Covers the shared streaming algorithm extracted in #495 (Slice 2 of #468).
All tests use mock PlatformCallbacks — no platform SDK imports required.

Updated in Slice 5 (#1192): all v1 TextRenderEvent / ToolSummaryRenderEvent
usages replaced with v2 TextDeltaRenderEvent / TextEndRenderEvent events.
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
from lyra.outbound.emitter import PlatformCallbacks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_callbacks(**overrides) -> PlatformCallbacks:
    """Build PlatformCallbacks with AsyncMock/MagicMock defaults."""
    cb = PlatformCallbacks(
        send_placeholder=AsyncMock(return_value=(object(), 42)),
        edit_placeholder_text=AsyncMock(),
        send_trace_placeholder=AsyncMock(return_value=(object(), 42)),
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
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    assert cb.edit_placeholder_text.call_args == ((placeholder_obj, "hello"),)
    cb.send_message.assert_not_called()
    cb.cancel_typing.assert_called_once()


async def test_stream_error_no_text():
    """When the event iterator raises, edit placeholder with descriptive error."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(RuntimeError, match="boom"):
        await session.run(_error_events())

    # Error text shows exception class name only; the original exception's str()
    # is NOT included (PR #1210 review B3 — prevents leaking hostnames, paths,
    # auth tokens that httpx/aiohttp/NATS exceptions may carry).
    args = cb.edit_placeholder_text.call_args[0]
    assert args[0] is placeholder_obj
    assert "RuntimeError" in args[1]
    assert "boom" not in args[1]
    assert "Please try again" in args[1]


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


async def test_empty_stream_surfaces_generic_error():
    """Terminal invariant: empty stream → placeholder edited to GENERIC_ERROR_REPLY."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    # Emit only a TextEnd with no prior delta — no text content
    await session.run(
        _events(
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    cb.edit_placeholder_text.assert_called_once_with(
        placeholder_obj,
        GENERIC_ERROR_REPLY,
    )


async def test_partial_text_then_stream_error():
    """Partial delta + stream error: no final text → descriptive error message."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(RuntimeError, match="mid-stream"):
        await session.run(_partial_then_error())

    # No TextEnd arrived — descriptive error with exception class name only.
    # The original exception's str() is NOT included (PR #1210 review B3 —
    # prevents leaking hostnames, paths, auth tokens via str(exc)).
    args = cb.edit_placeholder_text.call_args[0]
    assert args[0] is placeholder_obj
    assert "RuntimeError" in args[1]
    assert "mid-stream" not in args[1]
    assert "Please try again" in args[1]


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
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="fallback text"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

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
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="some text"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

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
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    assert outbound.metadata["reply_message_id"] == 42


async def test_reply_message_id_without_outbound():
    """No crash when outbound is None — reply_message_id tracking simply skipped."""
    cb = _make_callbacks()
    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hello"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )
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
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hi"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    cb.start_typing.assert_called_once()
    cb.cancel_typing.assert_not_called()


async def test_typing_tail_final():
    """cancel_typing called (not start_typing) when outbound.intermediate=False."""
    outbound = OutboundMessage.from_text("x")
    outbound.intermediate = False
    cb = _make_callbacks()

    session = StreamingSession(cb, outbound=outbound)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="hi"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    cb.cancel_typing.assert_called_once()
    cb.start_typing.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — trace placeholder
# ---------------------------------------------------------------------------

# Removed: test_trace_placeholder_not_sent_on_text_only — replaced with v2 equivalent


async def test_trace_placeholder_not_sent_on_text_only():
    """send_trace_placeholder NOT called on text-only turns (v2 events)."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="done"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    cb.send_trace_placeholder.assert_not_called()


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
    await session.run(
        _events(
            TextDeltaRenderEvent(message_id="msg1", delta="chunk1chunk2"),
            TextEndRenderEvent(message_id="msg1"),
        )
    )

    cb.edit_placeholder_text.assert_called_with(placeholder_obj, "chunk1")
    cb.send_message.assert_called_once_with("chunk2")


# ---------------------------------------------------------------------------
# Tests — get_msg (i18n)
# ---------------------------------------------------------------------------


async def test_get_msg_used_for_display_text():
    """get_msg callback is used by build_display_text for i18n strings."""

    async def _final_then_error():
        yield TextDeltaRenderEvent(message_id="msg1", delta="partial answer")
        yield TextEndRenderEvent(message_id="msg1")
        raise RuntimeError("late error")

    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))
    cb.get_msg = MagicMock(
        side_effect=lambda key, fallback: (
            " [interrompu]" if key == "stream_interrupted" else fallback
        ),
    )

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(RuntimeError, match="late error"):
        await session.run(_final_then_error())

    # build_display_text should have used get_msg for the interrupt suffix
    cb.get_msg.assert_called()
    # The final text should include the localised interrupt suffix
    cb.edit_placeholder_text.assert_called_with(
        placeholder_obj,
        "partial answer [interrompu]",
    )


async def test_get_msg_default_fallback():
    """When get_msg returns the fallback, default English strings are used."""
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    session = StreamingSession(cb, outbound=None)
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
    assert cb.edit_placeholder_text.call_args == ((placeholder_obj, "hello"),)


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
    cb = _make_callbacks()
    placeholder_obj = object()
    cb.send_placeholder = AsyncMock(return_value=(placeholder_obj, 42))

    async def _timeout_events():
        raise StreamChunkTimeout("no chunk received for 120s (stream_id='test')")
        yield  # make it an async generator  # noqa: RET503

    session = StreamingSession(cb, outbound=None)
    with pytest.raises(StreamChunkTimeout):
        await session.run(_timeout_events())

    args = cb.edit_placeholder_text.call_args[0]
    assert args[0] is placeholder_obj
    assert "120 s" in args[1]
