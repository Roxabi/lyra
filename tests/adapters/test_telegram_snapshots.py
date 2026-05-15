"""Snapshot tests: telegram message body for three scenarios (Slice 5 / #1192).

Re-recorded in Slice 5 (#1192) after v1 Text/ToolSummary RenderEvent removal.
Input streams now use v2 events only (TextStart/Delta/End triplet + Run lifecycle).

Scenarios:
  - test_single_block_snapshot:  text-only turn, no tools
  - test_multi_block_snapshot:   ToolCall* followed by final text (no ToolSummary)
  - test_error_snapshot:         text-only turn via v2 triplet

Snapshot machinery: inline string-literal assertions (no syrupy dependency).
The assertions capture the MarkdownV2-escaped text that edit_message_text
receives as its final call.
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
from tests.adapters.conftest import _make_telegram_adapter, _make_telegram_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter_with_bot():
    """Return (adapter, bot_mock) with send_message + edit_message_text mocked."""
    adapter = _make_telegram_adapter()
    placeholder = MagicMock()
    placeholder.message_id = 999
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=placeholder)
    bot.edit_message_text = AsyncMock()
    adapter.bot = bot
    return adapter, bot


def _last_edit_text(bot: AsyncMock) -> str:
    """Return the `text` kwarg from the last edit_message_text call."""
    call = bot.edit_message_text.call_args
    assert call is not None, "edit_message_text was never called"
    return call.kwargs.get("text", "") or (call.args[0] if call.args else "")


# ---------------------------------------------------------------------------
# Snapshot tests — v2 events (re-recorded Slice 5 / #1192)
# ---------------------------------------------------------------------------


class TestTelegramSnapshots:
    """Baseline snapshots of the v2-only telegram rendered output (post-Slice-5).

    Re-recorded in Slice 5 (#1192) after TextRenderEvent / ToolSummaryRenderEvent
    removal. Input streams use TextStart/Delta/End triplet only.
    """

    @pytest.mark.asyncio
    async def test_single_block_snapshot(self) -> None:
        """Single text block: final edit_message_text text matches snapshot.

        Input stream (v2): RunStarted, TextStart, TextDelta("Hello world!"),
                           TextEnd, RunFinished
        Expected rendered text: MarkdownV2-escaped "Hello world\\!"
        """
        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(message_id="msg-1", delta="Hello world!")
            yield TextEndRenderEvent(message_id="msg-1")
            yield RunFinishedRenderEvent(run_id="r1")

        await adapter.send_streaming(msg, _events())

        # Exact-match (B5 fix #1205): pinned to catch MarkdownV2 escape
        # regressions. `!` must be escaped → `\!` in Telegram MarkdownV2.
        final_text = _last_edit_text(bot)
        assert final_text == "Hello world\\!", f"Snapshot mismatch: {final_text!r}"

    @pytest.mark.asyncio
    async def test_multi_block_snapshot(self) -> None:
        """Multi-block turn: ToolCall* events followed by final text (v2).

        Input stream (v2):
          RunStarted, ToolCallStart, ToolCallEnd, TextStart,
          TextDelta("Tests passed."), TextEnd, RunFinished

        No ToolSummaryRenderEvent in stream (removed in Slice 5).
        edit_trace must NOT be called. Final text delivered via placeholder edit.
        """
        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield ToolCallStartRenderEvent(tool_call_id="t1", tool_name="Bash")
            yield ToolCallEndRenderEvent(tool_call_id="t1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(message_id="msg-1", delta="Tests passed.")
            yield TextEndRenderEvent(message_id="msg-1")
            yield RunFinishedRenderEvent(run_id="r1")

        await adapter.send_streaming(msg, _events())

        # Final text delivered
        last_text = _last_edit_text(bot)
        assert "Tests passed" in last_text, (
            f"Expected 'Tests passed' in final edit, got: {last_text!r}"
        )

    @pytest.mark.asyncio
    async def test_error_snapshot(self) -> None:
        """Soft error (B6 fix #1205): RunErrorRenderEvent threads is_error to ❌.

        Input stream (v2): RunStarted, TextStart, TextDelta("Something went wrong."),
                           TextEnd, RunError
        The RunErrorRenderEvent sets is_error_pending on the StreamState; the
        TextEndRenderEvent that precedes it has already captured the final text
        with is_error=False, but the order in this test mirrors the production
        flow where RunErrorRenderEvent arrives BEFORE the close — emitted by
        stream_processor's post-finally branch.

        Expected: final edit_message_text starts with ❌ (error prefix).
        """
        from lyra.core.messaging.render_events import RunErrorRenderEvent

        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(
                message_id="msg-1", delta="Something went wrong."
            )
            # RunError BEFORE TextEnd so is_error_pending is set when TextEnd
            # closes the block — mirrors stream_processor's
            # _close_text_block_on_result → RunError emission order.
            yield RunErrorRenderEvent(
                run_id="r1", message="model_error", code=None
            )
            yield TextEndRenderEvent(message_id="msg-1")

        await adapter.send_streaming(msg, _events())

        final_text = _last_edit_text(bot)
        # Exact-match: ❌ prefix prepended, then MarkdownV2-escaped content.
        # Telegram escapes `.` as `\.`.
        assert final_text == "❌ Something went wrong\\.", (
            f"Expected ❌-prefixed error text, got: {final_text!r}"
        )
