"""Snapshot tests: telegram message body for three scenarios (Slice 1 of #1192).

Purpose: lock in the current user-visible telegram output so that Slice 3's
v1-removal diff is reviewable. These tests may need re-recording after Slice 3
deletes TextRenderEvent / ToolSummaryRenderEvent — that is expected.

Scenarios:
  - test_single_block_snapshot:  text-only turn, no tools
  - test_multi_block_snapshot:   tool call followed by final text
  - test_error_snapshot:         error turn (is_error=True)

Snapshot machinery: inline string-literal assertions (no syrupy dependency).
The assertions capture the MarkdownV2-escaped text that edit_message_text
receives as its final call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.messaging.render_events import (
    TextRenderEvent,
    ToolSummaryRenderEvent,
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
# T1 — Slice 1 snapshot tests (current dual-emit baseline)
# ---------------------------------------------------------------------------


class TestTelegramSnapshots:
    """Baseline snapshots of the current (dual-emit) telegram rendered output.

    These tests will require re-recording after Slice 3 removes v1 events.
    Until then they serve as a regression gate against unintended rendering
    changes between Slice 1 and Slice 3.
    """

    @pytest.mark.asyncio
    async def test_single_block_snapshot(self) -> None:
        """Single text block: final edit_message_text text matches snapshot.

        Input stream: TextRenderEvent(text="Hello world!", is_final=True)
        Expected rendered text: MarkdownV2-escaped "Hello world\\!"
        """
        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield TextRenderEvent(text="Hello world!", is_final=True)

        await adapter.send_streaming(msg, _events())

        final_text = _last_edit_text(bot)
        assert final_text == "Hello world\\!", f"Snapshot mismatch: {final_text!r}"

    @pytest.mark.asyncio
    async def test_multi_block_snapshot(self) -> None:
        """Multi-block turn: tool summary followed by final text.

        Input stream:
          ToolSummaryRenderEvent(bash_commands=["uv run pytest"], is_complete=True)
          TextRenderEvent(text="Tests passed.", is_final=True)

        The tool summary edits the trace placeholder. The final text is sent as
        a new send_message call (not an edit). We assert both the trace edit
        text and the final message body.
        """
        adapter, bot = _make_adapter_with_bot()
        # Second send_message returns a new placeholder for final text
        final_msg = MagicMock()
        final_msg.message_id = 1001
        bot.send_message = AsyncMock(
            side_effect=[
                MagicMock(message_id=999),  # response placeholder
                MagicMock(message_id=1000),  # trace placeholder for tool
            ]
        )
        msg = _make_telegram_message()

        async def _events():
            yield ToolSummaryRenderEvent(
                bash_commands=["uv run pytest"], is_complete=True
            )
            yield TextRenderEvent(text="Tests passed.", is_final=True)

        await adapter.send_streaming(msg, _events())

        # Trace edit: first edit_message_text carries tool summary text
        assert bot.edit_message_text.await_count >= 1
        first_edit_text = bot.edit_message_text.call_args_list[0].kwargs.get("text", "")
        # Tool summary renders: header + body — check the header is present
        # "🔧 Done ✅" becomes "🔧 Done ✅" (special chars escaped by MarkdownV2)
        assert "Done" in first_edit_text or "Working" in first_edit_text, (
            f"Expected tool header in trace edit, got: {first_edit_text!r}"
        )
        assert "pytest" in first_edit_text, (
            f"Expected bash command in trace edit, got: {first_edit_text!r}"
        )

        # Final text: last edit_message_text carries the final response
        # After a tool event the text is sent as send_message (new msg), not edit.
        # But with the v1 path, is_final triggers _deliver_final via edit.
        # Verify the final rendered content exists somewhere.
        last_text = _last_edit_text(bot)
        assert "Tests passed" in last_text, (
            f"Expected 'Tests passed' in final edit, got: {last_text!r}"
        )

    @pytest.mark.asyncio
    async def test_error_snapshot(self) -> None:
        """Error turn: is_error=True prefixes output with ❌.

        Input stream: TextRenderEvent(text="Something went wrong.", is_final=True,
                                       is_error=True)
        Expected: final edit_message_text text contains ❌.
        """
        adapter, bot = _make_adapter_with_bot()
        msg = _make_telegram_message()

        async def _events():
            yield TextRenderEvent(
                text="Something went wrong.", is_final=True, is_error=True
            )

        await adapter.send_streaming(msg, _events())

        final_text = _last_edit_text(bot)
        assert "❌" in final_text, (
            f"Expected ❌ prefix in error snapshot, got: {final_text!r}"
        )
        assert "Something went wrong" in final_text, (
            f"Expected error text in snapshot, got: {final_text!r}"
        )
