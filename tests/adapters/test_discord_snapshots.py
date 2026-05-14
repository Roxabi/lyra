"""Snapshot tests: discord message body for three scenarios (Slice 1 of #1192).

Purpose: lock in the current user-visible discord output so that Slice 3's
v1-removal diff is reviewable. These tests may need re-recording after Slice 3
deletes TextRenderEvent / ToolSummaryRenderEvent — that is expected.

Scenarios:
  - test_single_block_snapshot:  text-only turn, no tools
  - test_multi_block_snapshot:   tool call followed by final text
  - test_error_snapshot:         error turn (is_error=True)

Snapshot machinery: inline string-literal / attribute assertions (no syrupy).
The assertions capture the content/embed passed to placeholder.edit on the final
call, which is the user-visible output in Discord.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.core.messaging.render_events import (
    TextRenderEvent,
    ToolSummaryRenderEvent,
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
# T2 — Slice 1 snapshot tests (current dual-emit baseline)
# ---------------------------------------------------------------------------


class TestDiscordSnapshots:
    """Baseline snapshots of the current (dual-emit) discord rendered output.

    These tests will require re-recording after Slice 3 removes v1 events.
    Until then they serve as a regression gate against unintended rendering
    changes between Slice 1 and Slice 3.
    """

    @pytest.mark.asyncio
    async def test_single_block_snapshot(self) -> None:
        """Single text block: final placeholder.edit content matches snapshot.

        Input stream: TextRenderEvent(text="Hello world!", is_final=True)
        Expected: final placeholder.edit(content="Hello world!")
        Discord does NOT apply MarkdownV2 escaping (unlike Telegram).
        """
        adapter = _make_discord_adapter()
        _, placeholder = _attach_channel(adapter)
        msg = make_dc_inbound_msg()

        async def _events():
            yield TextRenderEvent(text="Hello world!", is_final=True)

        await adapter.send_streaming(msg, _events())

        final_content = _last_edit_content(placeholder)
        assert final_content == "Hello world!", f"Snapshot mismatch: {final_content!r}"

    @pytest.mark.asyncio
    async def test_multi_block_snapshot(self) -> None:
        """Multi-block turn: tool summary embed followed by final text.

        Input stream:
          ToolSummaryRenderEvent(bash_commands=["uv run pytest"], is_complete=True)
          TextRenderEvent(text="Tests passed.", is_final=True)

        The tool summary must produce a discord.Embed with:
          - title matching "🔧 Done ✅"
          - description containing the bash command "uv run pytest"
          - color = discord.Color.green() (0x2ecc71 = 3066993)

        The final text edit is captured as content on the response placeholder.
        """
        adapter = _make_discord_adapter()
        channel, placeholder = _attach_channel(adapter)

        # Trace placeholder send goes through channel.send
        trace_placeholder = AsyncMock()
        trace_placeholder.id = 888
        trace_placeholder.edit = AsyncMock()
        channel.send = AsyncMock(return_value=trace_placeholder)

        msg = make_dc_inbound_msg()

        async def _events():
            yield ToolSummaryRenderEvent(
                bash_commands=["uv run pytest"], is_complete=True
            )
            yield TextRenderEvent(text="Tests passed.", is_final=True)

        await adapter.send_streaming(msg, _events())

        # Trace embed snapshot: channel.send called for trace placeholder
        assert channel.send.await_count >= 1

        # Tool embed on trace_placeholder.edit
        embed_edits = [
            c
            for c in trace_placeholder.edit.call_args_list
            if c.kwargs.get("embed") is not None
        ]
        assert len(embed_edits) >= 1, "Expected at least one embed edit on trace"
        embed: discord.Embed = embed_edits[0].kwargs["embed"]
        assert isinstance(embed, discord.Embed)
        # Title snapshot: "🔧 Done ✅"
        assert embed.title == "🔧 Done ✅", (
            f"Embed title snapshot mismatch: {embed.title!r}"
        )
        # Description snapshot: contains bash command
        assert embed.description is not None
        assert "pytest" in embed.description, (
            f"Expected 'pytest' in embed description: {embed.description!r}"
        )
        # Color snapshot: green (is_complete=True)
        assert embed.color == discord.Color.green(), (
            f"Expected green embed for complete tool, got: {embed.color!r}"
        )

        # Final text snapshot: response placeholder edited with text content
        final_content = _last_edit_content(placeholder)
        assert "Tests passed" in final_content, (
            f"Expected 'Tests passed' in final content, got: {final_content!r}"
        )

    @pytest.mark.asyncio
    async def test_error_snapshot(self) -> None:
        """Error turn: is_error=True prefixes output with ❌.

        Input stream: TextRenderEvent(text="Something went wrong.", is_final=True,
                                       is_error=True)
        Expected: final placeholder.edit content starts with ❌.
        """
        adapter = _make_discord_adapter()
        _, placeholder = _attach_channel(adapter)
        msg = make_dc_inbound_msg()

        async def _events():
            yield TextRenderEvent(
                text="Something went wrong.", is_final=True, is_error=True
            )

        await adapter.send_streaming(msg, _events())

        final_content = _last_edit_content(placeholder)
        assert "❌" in final_content, (
            f"Expected ❌ prefix in error snapshot, got: {final_content!r}"
        )
        assert "Something went wrong" in final_content, (
            f"Expected error text in snapshot, got: {final_content!r}"
        )
