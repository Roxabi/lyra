"""Snapshot tests: discord message body for three scenarios (Slice 5 / #1192 re-record).

Re-recorded in Slice 5 (#1192) after v1 Text/ToolSummary RenderEvent removal.
Input streams now use v2 events only (TextStart/Delta/End triplet + Run lifecycle).

Scenarios:
  - test_single_block_snapshot:  text-only turn, no tools
  - test_multi_block_snapshot:   ToolCall* followed by final text (no ToolSummary)
  - test_error_snapshot:         text-only turn via v2 triplet

Snapshot machinery: inline string-literal / attribute assertions (no syrupy).
The assertions capture the content/embed passed to placeholder.edit on the final
call, which is the user-visible output in Discord.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.messaging.render_events import (
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallStartRenderEvent,
)
from tests.adapters.conftest import make_dc_inbound_msg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discord_adapter():
    """Build a DiscordAdapter with placeholder + channel mocked."""
    from lyra.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
    )
    return adapter


def _attach_channel(adapter, placeholder_id: int = 777):
    """Wire a mock channel + placeholder onto *adapter*.

    Returns (channel, placeholder).
    """
    placeholder = AsyncMock()
    placeholder.id = placeholder_id
    placeholder.edit = AsyncMock()

    trigger_msg = AsyncMock()
    trigger_msg.reply = AsyncMock(return_value=placeholder)

    channel = MagicMock()
    channel.get_partial_message = MagicMock(return_value=trigger_msg)
    channel.send = AsyncMock(return_value=placeholder)

    adapter.get_channel = MagicMock(return_value=channel)
    return channel, placeholder


def _last_edit_content(placeholder: AsyncMock) -> str:
    """Return the `content` kwarg from the last placeholder.edit call."""
    call = placeholder.edit.call_args
    assert call is not None, "placeholder.edit was never called"
    return call.kwargs.get("content", "")


# ---------------------------------------------------------------------------
# Snapshot tests — v2 events (re-recorded Slice 5 / #1192)
# ---------------------------------------------------------------------------


class TestDiscordSnapshots:
    """Baseline snapshots of the v2-only discord rendered output (post-Slice-5).

    Re-recorded in Slice 5 (#1192) after TextRenderEvent / ToolSummaryRenderEvent
    removal. Input streams use TextStart/Delta/End triplet only.
    """

    @pytest.mark.asyncio
    async def test_single_block_snapshot(self) -> None:
        """Single text block: final placeholder.edit content matches snapshot.

        Input stream (v2): RunStarted, TextStart, TextDelta("Hello world!"),
                           TextEnd, RunFinished
        Discord does NOT apply MarkdownV2 escaping (unlike Telegram).
        """
        adapter = _make_discord_adapter()
        _, placeholder = _attach_channel(adapter)
        msg = make_dc_inbound_msg()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(message_id="msg-1", delta="Hello world!")
            yield TextEndRenderEvent(message_id="msg-1")
            yield RunFinishedRenderEvent(run_id="r1")

        await adapter.send_streaming(msg, _events())

        # Exact-match (B5 fix #1205): pinned snapshot. Discord does NOT apply
        # MarkdownV2 escaping; `!` passes through unchanged.
        final_content = _last_edit_content(placeholder)
        assert final_content == "Hello world!", (
            f"Snapshot mismatch: {final_content!r}"
        )

    @pytest.mark.asyncio
    async def test_multi_block_snapshot(self) -> None:
        """Multi-block turn: ToolCall* events followed by final text (v2).

        Input stream (v2):
          RunStarted, ToolCallStart, ToolCallEnd, TextStart,
          TextDelta("Tests passed."), TextEnd, RunFinished

        No ToolSummaryRenderEvent in stream (removed in Slice 5).
        No embed edit expected. Final text delivered via placeholder.edit content.
        """
        adapter = _make_discord_adapter()
        _, placeholder = _attach_channel(adapter)

        msg = make_dc_inbound_msg()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Bash")
            yield ToolCallEndRenderEvent(tool_call_id="t1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(message_id="msg-1", delta="Tests passed.")
            yield TextEndRenderEvent(message_id="msg-1")
            yield RunFinishedRenderEvent(run_id="r1")

        await adapter.send_streaming(msg, _events())

        # Final text snapshot: response placeholder edited with text content
        final_content = _last_edit_content(placeholder)
        assert "Tests passed" in final_content, (
            f"Expected 'Tests passed' in final content, got: {final_content!r}"
        )

    @pytest.mark.asyncio
    async def test_error_snapshot(self) -> None:
        """Soft error (B6 fix #1205): RunErrorRenderEvent threads is_error to ❌.

        Input stream (v2): RunStarted, TextStart, TextDelta("Something went wrong."),
                           RunError, TextEnd
        Mirrors stream_processor's order: RunError fires after the loop closes the
        text block via TextEnd; in this synthetic stream we put RunError BEFORE
        TextEnd so is_error_pending is captured when the close drives set_final_text.

        Expected: final placeholder.edit starts with ❌ prefix.
        """
        from lyra.core.messaging.render_events import RunErrorRenderEvent

        adapter = _make_discord_adapter()
        _, placeholder = _attach_channel(adapter)
        msg = make_dc_inbound_msg()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(
                message_id="msg-1", delta="Something went wrong."
            )
            yield RunErrorRenderEvent(
                run_id="r1", message="model_error", code=None
            )
            yield TextEndRenderEvent(message_id="msg-1")

        await adapter.send_streaming(msg, _events())

        # Exact-match: ❌ prefix prepended; Discord does NOT escape `.`.
        final_content = _last_edit_content(placeholder)
        assert final_content == "❌ Something went wrong.", (
            f"Expected ❌-prefixed error text, got: {final_content!r}"
        )
