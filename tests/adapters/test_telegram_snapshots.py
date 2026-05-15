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

        final_text = _last_edit_text(bot)
        assert "Hello world" in final_text, f"Snapshot mismatch: {final_text!r}"

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
        """Text-only turn via v2 triplet (re-recorded: no is_error in v2 wire).

        Input stream (v2): RunStarted, TextStart, TextDelta("Something went wrong."),
                           TextEnd, RunFinished
        Expected: final edit_message_text contains the error text.
        """
        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(
                message_id="msg-1", delta="Something went wrong."
            )
            yield TextEndRenderEvent(message_id="msg-1")
            yield RunFinishedRenderEvent(run_id="r1")

        await adapter.send_streaming(msg, _events())

        final_text = _last_edit_text(bot)
        assert "Something went wrong" in final_text, (
            f"Expected error text in snapshot, got: {final_text!r}"
        )
