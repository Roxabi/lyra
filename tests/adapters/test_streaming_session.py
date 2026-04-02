"""Tests for StreamingSession and PlatformCallbacks.

These are RED-phase tests: the module under test
(lyra.adapters._shared_streaming) does not exist yet.
All tests are expected to fail with ImportError until V2 is implemented.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from lyra.adapters._shared_streaming import PlatformCallbacks, StreamingSession
from lyra.core.message import OutboundMessage
from lyra.core.render_events import TextRenderEvent, ToolSummaryRenderEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_callbacks(**overrides: Any) -> PlatformCallbacks:
    """Return a PlatformCallbacks with all fields mocked to sensible defaults.

    - send_placeholder: AsyncMock returning (MagicMock(), 42)
    - edit_placeholder_text: AsyncMock returning None
    - edit_placeholder_tool: AsyncMock returning None
    - send_message: AsyncMock returning 99
    - send_fallback: AsyncMock returning 77
    - chunk_text: MagicMock (sync) returning [text] (identity)
    - start_typing: MagicMock (sync) returning None
    - cancel_typing: MagicMock (sync) returning None

    Pass keyword arguments to override specific fields.
    """
    placeholder_obj = MagicMock()

    defaults: dict[str, Any] = dict(
        send_placeholder=AsyncMock(return_value=(placeholder_obj, 42)),
        edit_placeholder_text=AsyncMock(return_value=None),
        edit_placeholder_tool=AsyncMock(return_value=None),
        send_message=AsyncMock(return_value=99),
        send_fallback=AsyncMock(return_value=77),
        chunk_text=MagicMock(side_effect=lambda text: [text]),
        start_typing=MagicMock(return_value=None),
        cancel_typing=MagicMock(return_value=None),
    )
    defaults.update(overrides)
    return PlatformCallbacks(**defaults)


async def text_events(*texts: str, is_final_last: bool = True):
    """Yield TextRenderEvents; the last one has is_final=True."""
    for i, t in enumerate(texts):
        yield TextRenderEvent(
            text=t,
            is_final=(i == len(texts) - 1 and is_final_last),
        )


async def tool_then_text(
    tool_summary: str = "tool result",
    final_text: str = "done",
):
    """Yield one ToolSummaryRenderEvent then a final TextRenderEvent."""
    yield ToolSummaryRenderEvent(bash_commands=["make test"], is_complete=True)
    yield TextRenderEvent(text=final_text, is_final=True)


async def empty_events():
    """Empty async generator — yields nothing."""
    return
    yield  # make it an async generator


# ---------------------------------------------------------------------------
# TestStreamingSessionPlaceholder
# ---------------------------------------------------------------------------


class TestStreamingSessionPlaceholder:
    async def test_send_placeholder_sets_reply_message_id(self) -> None:
        """send_placeholder reply_id is stored in outbound.metadata."""
        # Arrange
        outbound = OutboundMessage.from_text("")
        callbacks = make_mock_callbacks(
            send_placeholder=AsyncMock(return_value=(MagicMock(), 42))
        )
        session = StreamingSession(callbacks=callbacks, outbound=outbound)

        # Act
        await session.run(text_events("hello"))

        # Assert
        assert outbound.metadata["reply_message_id"] == 42

    async def test_send_placeholder_none_outbound_no_crash(self) -> None:
        """StreamingSession with outbound=None must not raise."""
        # Arrange
        callbacks = make_mock_callbacks()
        session = StreamingSession(callbacks=callbacks, outbound=None)

        # Act / Assert — no exception
        await session.run(text_events("hello"))


# ---------------------------------------------------------------------------
# TestStreamingSessionFallback
# ---------------------------------------------------------------------------


class TestStreamingSessionFallback:
    async def test_fallback_path_updates_reply_message_id(self) -> None:
        """When send_placeholder raises, fallback id is stored in outbound.metadata."""
        # Arrange
        outbound = OutboundMessage.from_text("")
        callbacks = make_mock_callbacks(
            send_placeholder=AsyncMock(side_effect=Exception("network error")),
            send_fallback=AsyncMock(return_value=77),
        )
        session = StreamingSession(callbacks=callbacks, outbound=outbound)

        # Act
        await session.run(text_events("hello"))

        # Assert
        assert outbound.metadata["reply_message_id"] == 77

    async def test_fallback_path_drains_all_events(self) -> None:
        """When send_placeholder raises, remaining events are drained without crash."""
        # Arrange
        _send_fallback = AsyncMock(return_value=77)
        callbacks = make_mock_callbacks(
            send_placeholder=AsyncMock(side_effect=Exception("network error")),
            send_fallback=_send_fallback,
        )
        session = StreamingSession(callbacks=callbacks, outbound=None)

        # Act — multiple events should all be consumed without error
        await session.run(text_events("a", "b", "c"))

        # Assert — fallback was called (session completed gracefully)
        _send_fallback.assert_called_once()


# ---------------------------------------------------------------------------
# TestStreamingSessionToolEvents
# ---------------------------------------------------------------------------


class TestStreamingSessionToolEvents:
    async def test_had_tool_events_sends_new_message(self) -> None:
        """After ToolSummaryRenderEvent, final text is sent as a new message."""
        # Arrange
        outbound = OutboundMessage.from_text("")
        _send_message = AsyncMock(return_value=99)
        callbacks = make_mock_callbacks(
            send_message=_send_message,
        )
        session = StreamingSession(callbacks=callbacks, outbound=outbound)

        # Act
        await session.run(tool_then_text("summary", "final"))

        # Assert
        _send_message.assert_called()
        assert outbound.metadata["reply_message_id"] == 99

    async def test_text_only_edits_placeholder(self) -> None:
        """Text-only turn (no tool events) edits the placeholder, not send_message."""
        # Arrange
        _edit_placeholder_text = AsyncMock(return_value=None)
        _send_message = AsyncMock(return_value=99)
        callbacks = make_mock_callbacks(
            edit_placeholder_text=_edit_placeholder_text,
            send_message=_send_message,
        )
        session = StreamingSession(callbacks=callbacks, outbound=None)

        # Act
        await session.run(text_events("hello"))

        # Assert
        _edit_placeholder_text.assert_called()
        _send_message.assert_not_called()


# ---------------------------------------------------------------------------
# TestStreamingSessionTyping
# ---------------------------------------------------------------------------


class TestStreamingSessionTyping:
    async def test_cancel_typing_called_when_not_intermediate(self) -> None:
        """cancel_typing is called when outbound.intermediate is False (default)."""
        # Arrange
        outbound = OutboundMessage.from_text("")
        # intermediate defaults to False
        _cancel_typing = MagicMock(return_value=None)
        callbacks = make_mock_callbacks(cancel_typing=_cancel_typing)
        session = StreamingSession(callbacks=callbacks, outbound=outbound)

        # Act
        await session.run(text_events("hello"))

        # Assert
        _cancel_typing.assert_called()

    async def test_start_typing_called_when_intermediate(self) -> None:
        """start_typing is called when outbound.intermediate is True."""
        # Arrange
        outbound = OutboundMessage.from_text("")
        outbound.intermediate = True
        _start_typing = MagicMock(return_value=None)
        callbacks = make_mock_callbacks(start_typing=_start_typing)
        session = StreamingSession(callbacks=callbacks, outbound=outbound)

        # Act
        await session.run(text_events("hello"))

        # Assert
        _start_typing.assert_called()

    async def test_cancel_typing_called_when_outbound_is_none(self) -> None:
        """cancel_typing is called when outbound is None (no intermediate flag)."""
        # Arrange
        _cancel_typing = MagicMock(return_value=None)
        callbacks = make_mock_callbacks(cancel_typing=_cancel_typing)
        session = StreamingSession(callbacks=callbacks, outbound=None)

        # Act
        await session.run(text_events("hello"))

        # Assert — when outbound is None, not-intermediate path applies
        _cancel_typing.assert_called()
