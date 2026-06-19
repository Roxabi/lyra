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

from unittest.mock import AsyncMock

import pytest

from factory.core.messaging.render_events import (
    RunFinishedRenderEvent,
    RunStartedRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
    ToolCallEndRenderEvent,
    ToolCallStartRenderEvent,
)
from tests.adapters.conftest import (
    _make_telegram_adapter,
    _make_telegram_message,
    wire_telegram_rich_bot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter_with_bot():
    """Return (adapter, bot_mock) with rich-message mocks."""
    adapter = _make_telegram_adapter()
    bot = AsyncMock()
    wire_telegram_rich_bot(bot, message_id=999)
    adapter.bot = bot
    return adapter, bot


def _last_edit_text(bot: AsyncMock) -> str:
    """Return rich_message.markdown from the last edit_message_text call."""
    call = bot.edit_message_text.call_args
    assert call is not None, "edit_message_text was never called"
    rich = call.kwargs.get("rich_message")
    if rich is not None:
        return rich.markdown or ""
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
        Expected rendered text: unescaped "Hello world!"
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

        # Exact-match (B5 fix #1205): rich messages keep punctuation unescaped.
        final_text = _last_edit_text(bot)
        assert final_text == "Hello world!", f"Snapshot mismatch: {final_text!r}"

    @pytest.mark.asyncio
    async def test_multi_block_snapshot(self) -> None:
        """Multi-block turn: ToolCall* events followed by final text (v2).

        Input stream (v2):
          RunStarted, ToolCallStart, ToolCallEnd, TextStart,
          TextDelta("Tests passed."), TextEnd, RunFinished

        No ToolSummaryRenderEvent in stream (removed in Slice 5).
        Final text delivered via placeholder edit.
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
        """Soft error (B6 fix #1205): RunErrorRenderEvent triggers ❌ prefix.

        Input stream (v2) — matches stream_processor's actual emission order
        for the soft-error path (ResultLlmEvent.is_error=True):
          RunStarted → TextStart → TextDelta → TextEnd → RunError

        stream_processor closes the open text block via TextEnd inside the
        ResultLlmEvent branch, then emits RunErrorRenderEvent post-finally.
        The adapter's build_display_text() reads is_error_pending at delivery
        time (NOT at TextEnd capture time) so the ❌ prefix is applied even
        though TextEnd was processed before RunError fired.

        Expected: final edit_message_text starts with ❌ (error prefix).
        """
        from factory.core.messaging.render_events import RunErrorRenderEvent

        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield RunStartedRenderEvent(run_id="r1")
            yield TextStartRenderEvent(message_id="msg-1")
            yield TextDeltaRenderEvent(
                message_id="msg-1", delta="Something went wrong."
            )
            # Production order: TextEnd first (inside ResultLlmEvent branch),
            # then RunError post-finally. build_display_text consults
            # is_error_pending at delivery, so order does not matter.
            yield TextEndRenderEvent(message_id="msg-1")
            yield RunErrorRenderEvent(run_id="r1", message="model_error", code=None)

        await adapter.send_streaming(msg, _events())

        final_text = _last_edit_text(bot)
        # Exact-match: ❌ prefix prepended; rich markdown keeps `.` unescaped.
        assert final_text == "❌ Something went wrong.", (
            f"Expected ❌-prefixed error text, got: {final_text!r}"
        )
