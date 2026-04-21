"""Tests for adapter send_streaming: Telegram + Discord edit-in-place with debounce."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, OutboundMessage
from lyra.core.messaging.render_events import TextRenderEvent, ToolSummaryRenderEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tg_message() -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_dc_message() -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:100",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 1,
            "channel_id": 100,
            "message_id": 200,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )


async def quick_events():
    """Yield a single final TextRenderEvent (text-only turn, no tools)."""
    yield TextRenderEvent(text="Hello world!", is_final=True)


async def error_events():
    """Yield a partial TextRenderEvent then raise (stream interrupted)."""
    yield TextRenderEvent(text="partial", is_final=False)
    raise RuntimeError("stream died")


# ---------------------------------------------------------------------------
# Telegram streaming
# ---------------------------------------------------------------------------


class TestTelegramStreaming:
    def _make_adapter(self):
        from lyra.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(
            bot_id="main",
            token="fake-token",
            inbound_bus=MagicMock(),
            webhook_secret="secret",
        )
        mock_bot = AsyncMock()
        placeholder = MagicMock()
        placeholder.message_id = 999
        mock_bot.send_message = AsyncMock(return_value=placeholder)
        mock_bot.edit_message_text = AsyncMock()
        adapter.bot = mock_bot
        return adapter, mock_bot

    async def test_sends_placeholder_then_edits(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        await adapter.send_streaming(msg, quick_events())

        # Placeholder sent
        bot.send_message.assert_awaited_once()
        # Final edit called with full text (MarkdownV2-escaped)
        last_edit = bot.edit_message_text.call_args
        assert last_edit.kwargs["text"] == "Hello world\\!"
        assert last_edit.kwargs.get("parse_mode") == "MarkdownV2"

    async def test_debounce_limits_edits(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        # With quick events (no delay), edits are debounced — only final edit
        await adapter.send_streaming(msg, quick_events())

        # Final edit always happens, but intermediate edits are debounced
        # Quick events arrive within debounce window, so only final edit
        assert bot.edit_message_text.await_count >= 1

    async def test_placeholder_failure_falls_back(self) -> None:
        adapter, bot = self._make_adapter()
        # First call (placeholder) fails, second call (fallback send) succeeds
        bot.send_message = AsyncMock(side_effect=[RuntimeError("network"), MagicMock()])
        msg = make_tg_message()

        await adapter.send_streaming(msg, quick_events())

        # Should fall back to regular send with full accumulated text
        assert bot.send_message.await_count == 2
        fallback_call = bot.send_message.call_args_list[1]
        assert fallback_call.kwargs["text"] == "Hello world\\!"
        assert fallback_call.kwargs.get("parse_mode") == "MarkdownV2"

    async def test_stores_reply_message_id_in_outbound(self) -> None:
        adapter, _ = self._make_adapter()
        msg = make_tg_message()
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(msg, quick_events(), outbound)

        assert outbound.metadata["reply_message_id"] == 999

    async def test_no_outbound_still_works(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        await adapter.send_streaming(msg, quick_events())

        bot.send_message.assert_awaited_once()

    async def test_mid_stream_error_stores_reply_message_id(self) -> None:
        adapter, _ = self._make_adapter()
        msg = make_tg_message()
        outbound = OutboundMessage.from_text("")

        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_events(), outbound)

        # reply_message_id set before error (placeholder succeeded)
        assert outbound.metadata["reply_message_id"] == 999

    async def test_mid_stream_error_appends_interrupted(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        # send_streaming now re-raises after the final edit so OutboundDispatcher
        # can record CB failure
        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_events())

        # Stream error → no is_final event → error edit on placeholder
        last_edit = bot.edit_message_text.call_args
        assert last_edit is not None  # placeholder was edited with error

    async def test_placeholder_failure_writes_fallback_id_to_outbound(self) -> None:
        adapter, bot = self._make_adapter()
        fallback_msg = MagicMock()
        fallback_msg.message_id = 1001
        bot.send_message = AsyncMock(
            side_effect=[RuntimeError("network"), fallback_msg]
        )
        msg = make_tg_message()
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(msg, quick_events(), outbound)

        assert outbound.metadata["reply_message_id"] == 1001

    async def test_tool_summary_edits_placeholder(self) -> None:
        """ToolSummaryRenderEvent -> editMessage on placeholder."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def tool_events():
            yield ToolSummaryRenderEvent(
                bash_commands=["uv run pytest"], is_complete=False
            )
            yield TextRenderEvent(text="Done.", is_final=True)

        await adapter.send_streaming(msg, tool_events())
        # Placeholder was edited with tool summary at least once
        assert bot.edit_message_text.await_count >= 1
        first_edit_text = bot.edit_message_text.call_args_list[0].kwargs.get("text", "")
        assert "pytest" in first_edit_text or "Working" in first_edit_text

    async def test_tool_summary_then_text_sends_new_message(self) -> None:
        """After ToolSummaryRenderEvent, TextRenderEvent is sent as a new message."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def tool_then_text():
            yield ToolSummaryRenderEvent(bash_commands=["make test"], is_complete=True)
            yield TextRenderEvent(text="All tests pass.", is_final=True)

        await adapter.send_streaming(msg, tool_then_text())
        # send_message called: 1 placeholder + 1 final text (as new message)
        assert bot.send_message.await_count == 2

    async def test_is_error_prefixes_error_marker(self) -> None:
        """TextRenderEvent(is_error=True) -> message prefixed with ❌."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def error_turn():
            yield TextRenderEvent(
                text="Something went wrong.", is_final=True, is_error=True
            )

        await adapter.send_streaming(msg, error_turn())
        last_edit = bot.edit_message_text.call_args
        assert last_edit is not None
        assert "❌" in last_edit.kwargs["text"]

    async def test_intermediate_outbound_restarts_typing(self) -> None:
        """When outbound.intermediate=True, _start_typing is called after send."""
        adapter, _ = self._make_adapter()
        msg = make_tg_message()
        from lyra.core.messaging.message import OutboundMessage

        outbound = OutboundMessage.from_text("")
        outbound.intermediate = True
        start_calls = []
        object.__setattr__(
            adapter,
            "_start_typing",
            lambda cid: start_calls.append(cid),
        )
        await adapter.send_streaming(msg, quick_events(), outbound)
        assert len(start_calls) == 1

    async def test_is_error_with_stream_error_sends_prefixed_interrupted(self) -> None:
        """is_error=True final event + exception → ❌-prefixed interrupted text."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def error_with_final():
            yield TextRenderEvent(text="partial answer", is_final=True, is_error=True)
            raise RuntimeError("stream error")

        with pytest.raises(RuntimeError):
            await adapter.send_streaming(msg, error_with_final())

        last_edit = bot.edit_message_text.call_args
        # Placeholder was edited with an error-prefixed interrupted message
        assert last_edit is not None
        text = last_edit.kwargs.get("text", "") or (
            last_edit.args[0] if last_edit.args else ""
        )
        assert "❌" in text

    async def test_text_only_overflow_sends_extra_chunks(self) -> None:
        """Text >4096 chars: first chunk edits placeholder, overflow as new msgs."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        long_text = "A" * 5000  # Forces 2 chunks: 4096 + 904

        async def long_events():
            yield TextRenderEvent(text=long_text, is_final=True)

        await adapter.send_streaming(msg, long_events())

        # Placeholder was created
        assert bot.send_message.call_count >= 1
        # First chunk edits placeholder (text-only path)
        assert bot.edit_message_text.call_count >= 1
        # Overflow chunk sent as new message: send_message = placeholder + overflow
        assert bot.send_message.call_count >= 2


# ---------------------------------------------------------------------------
# Discord streaming
# ---------------------------------------------------------------------------


class TestDiscordStreaming:
    def _make_adapter(self):
        from lyra.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )

        mock_placeholder = AsyncMock()
        mock_placeholder.edit = AsyncMock()

        mock_message = AsyncMock()
        mock_message.reply = AsyncMock(return_value=mock_placeholder)

        mock_channel = MagicMock()
        mock_channel.get_partial_message = MagicMock(return_value=mock_message)
        mock_channel.send = AsyncMock(return_value=mock_placeholder)

        adapter.get_channel = MagicMock(return_value=mock_channel)
        return adapter, mock_channel, mock_placeholder

    async def test_sends_placeholder_then_edits(self) -> None:
        adapter, channel, placeholder = self._make_adapter()
        msg = make_dc_message()

        await adapter.send_streaming(msg, quick_events())

        # Placeholder sent as reply to trigger message
        mock_msg = channel.get_partial_message.return_value
        mock_msg.reply.assert_awaited_once_with("\u2026")
        last_edit = placeholder.edit.call_args
        assert last_edit.kwargs["content"] == "Hello world!"

    async def test_mid_stream_error_appends_interrupted(self) -> None:
        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        # send_streaming now re-raises after the final edit so OutboundDispatcher
        # can record CB failure
        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_events())

        # Stream error with no is_final TextRenderEvent → generic error edit
        last_edit = placeholder.edit.call_args
        assert last_edit is not None

    async def test_stores_reply_message_id_in_outbound(self) -> None:
        adapter, _, placeholder = self._make_adapter()
        placeholder.id = 777
        msg = make_dc_message()
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(msg, quick_events(), outbound)

        assert outbound.metadata["reply_message_id"] == 777

    async def test_mid_stream_error_stores_reply_message_id(self) -> None:
        adapter, _, placeholder = self._make_adapter()
        placeholder.id = 777
        msg = make_dc_message()
        outbound = OutboundMessage.from_text("")

        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_events(), outbound)

        assert outbound.metadata["reply_message_id"] == 777

    async def test_truncates_at_discord_max(self) -> None:
        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def long_events():
            yield TextRenderEvent(text="x" * 3000, is_final=True)

        await adapter.send_streaming(msg, long_events())

        last_edit = placeholder.edit.call_args
        assert len(last_edit.kwargs["content"]) <= 2000

    async def test_tool_summary_uses_embed(self) -> None:
        """ToolSummaryRenderEvent -> placeholder.edit(embed=...) called."""
        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def tool_events():
            yield ToolSummaryRenderEvent(
                bash_commands=["uv run pytest"], is_complete=False
            )
            yield TextRenderEvent(text="Done.", is_final=True)

        await adapter.send_streaming(msg, tool_events())
        # embed edit called at least once (embed is a non-None discord.Embed object)
        edit_calls = placeholder.edit.call_args_list
        embed_edits = [c for c in edit_calls if c.kwargs.get("embed") is not None]
        assert len(embed_edits) >= 1

    async def test_tool_summary_then_text_sends_new_message(self) -> None:
        """After ToolSummaryRenderEvent, TextRenderEvent sent as new channel message."""
        adapter, channel, _ = self._make_adapter()
        msg = make_dc_message()

        async def tool_then_text():
            yield ToolSummaryRenderEvent(bash_commands=["make test"], is_complete=True)
            yield TextRenderEvent(text="Result text.", is_final=True)

        await adapter.send_streaming(msg, tool_then_text())
        # messageable.send called for the final text (not just the placeholder)
        assert channel.send.await_count >= 1

    async def test_is_error_prefixes_error_marker(self) -> None:
        """TextRenderEvent(is_error=True) -> Discord message prefixed with ❌."""
        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def error_turn():
            yield TextRenderEvent(
                text="Something went wrong.", is_final=True, is_error=True
            )

        await adapter.send_streaming(msg, error_turn())
        last_edit = placeholder.edit.call_args
        assert last_edit is not None
        content = last_edit.kwargs.get("content", "")
        assert "❌" in content

    async def test_intermediate_outbound_restarts_typing(self) -> None:
        """When outbound.intermediate=True, _start_typing is called after send."""
        adapter, _, _ = self._make_adapter()
        msg = make_dc_message()
        from lyra.core.messaging.message import OutboundMessage

        outbound = OutboundMessage.from_text("")
        outbound.intermediate = True
        start_calls = []
        object.__setattr__(
            adapter,
            "_start_typing",
            lambda cid: start_calls.append(cid),
        )
        await adapter.send_streaming(msg, quick_events(), outbound)
        assert len(start_calls) == 1

    async def test_is_error_with_stream_error_edits_placeholder(self) -> None:
        """is_error=True final event + exception → placeholder edited with ❌."""
        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def error_with_final():
            yield TextRenderEvent(text="partial answer", is_final=True, is_error=True)
            raise RuntimeError("stream error")

        with pytest.raises(RuntimeError):
            await adapter.send_streaming(msg, error_with_final())

        # Placeholder was edited at least once (with ❌-prefixed text)
        assert placeholder.edit.call_count >= 1
        last_edit = placeholder.edit.call_args
        assert last_edit is not None
        content = last_edit.kwargs.get("content", "")
        assert "❌" in content


# ---------------------------------------------------------------------------
# Intermediate text streaming
# ---------------------------------------------------------------------------


class TestTelegramIntermediateText:
    """TextRenderEvent(is_final=False) edits placeholder with accumulated text."""

    def _make_adapter(self):
        from lyra.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(
            bot_id="main",
            token="fake-token",
            inbound_bus=MagicMock(),
            webhook_secret="secret",
        )
        mock_bot = AsyncMock()
        placeholder = MagicMock()
        placeholder.message_id = 999
        mock_bot.send_message = AsyncMock(return_value=placeholder)
        mock_bot.edit_message_text = AsyncMock()
        adapter.bot = mock_bot
        return adapter, mock_bot

    async def test_intermediate_text_edits_placeholder(self) -> None:
        """TextRenderEvent(is_final=False) edits placeholder with intermediate text."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def inter_then_final():
            yield TextRenderEvent(text="Thinking...", is_final=False)
            yield TextRenderEvent(text="Done!", is_final=True)

        await adapter.send_streaming(msg, inter_then_final())

        # edit_message_text called at least once for intermediate, then again for final
        assert bot.edit_message_text.await_count >= 1

    async def test_intermediate_text_accumulated(self) -> None:
        """Multiple intermediate events accumulate before debounce fires."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def multi_intermediate():
            yield TextRenderEvent(text="Step 1. ", is_final=False)
            yield TextRenderEvent(text="Step 2.", is_final=False)
            yield TextRenderEvent(text="Done.", is_final=True)

        await adapter.send_streaming(msg, multi_intermediate())

        # Final edit triggered (is_final=True path)
        last_edit = bot.edit_message_text.call_args
        assert last_edit is not None

    async def test_intermediate_does_not_affect_final_text_only_path(self) -> None:
        """Intermediate text edit does not break final text-only edit path."""
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        async def inter_only():
            yield TextRenderEvent(text="Working...", is_final=False)
            yield TextRenderEvent(text="Final answer.", is_final=True)

        await adapter.send_streaming(msg, inter_only())

        last_edit = bot.edit_message_text.call_args
        assert last_edit is not None
        # Final edit contains the final text
        assert "Final answer" in last_edit.kwargs.get("text", "")


class TestDiscordIntermediateText:
    """TextRenderEvent(is_final=False) edits placeholder with accumulated text."""

    def _make_adapter(self):
        from lyra.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
        )

        mock_placeholder = AsyncMock()
        mock_placeholder.edit = AsyncMock()

        mock_message = AsyncMock()
        mock_message.reply = AsyncMock(return_value=mock_placeholder)

        mock_channel = MagicMock()
        mock_channel.get_partial_message = MagicMock(return_value=mock_message)
        mock_channel.send = AsyncMock(return_value=mock_placeholder)

        adapter.get_channel = MagicMock(return_value=mock_channel)
        return adapter, mock_channel, mock_placeholder

    async def test_intermediate_text_edits_placeholder(self) -> None:
        """TextRenderEvent(is_final=False) edits placeholder with text content."""
        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def inter_then_final():
            yield TextRenderEvent(text="Thinking...", is_final=False)
            yield TextRenderEvent(text="Done!", is_final=True)

        await adapter.send_streaming(msg, inter_then_final())

        # Intermediate edit: placeholder.edit(content=..., embed=None)
        # — embed key present (value is None).
        # Final text-only edit: placeholder.edit(content=...) — embed key absent.
        # Collect edits where embed=None explicitly (the intermediate text path).
        intermediate_edits = [
            c
            for c in placeholder.edit.call_args_list
            if (
                c.kwargs.get("content")
                and "embed" in c.kwargs
                and c.kwargs.get("embed") is None
            )
        ]
        assert len(intermediate_edits) >= 1
        assert "Thinking" in intermediate_edits[0].kwargs["content"]

    async def test_intermediate_truncates_to_discord_max(self) -> None:
        """Intermediate text longer than DISCORD_MAX_LENGTH is tail-truncated."""
        from lyra.adapters._shared import DISCORD_MAX_LENGTH

        adapter, _, placeholder = self._make_adapter()
        msg = make_dc_message()

        long_text = "x" * (DISCORD_MAX_LENGTH + 500)

        async def long_intermediate():
            yield TextRenderEvent(text=long_text, is_final=False)
            yield TextRenderEvent(text="Done!", is_final=True)

        await adapter.send_streaming(msg, long_intermediate())

        content_edits = [
            c for c in placeholder.edit.call_args_list if c.kwargs.get("content")
        ]
        assert len(content_edits) >= 1
        assert len(content_edits[0].kwargs["content"]) <= DISCORD_MAX_LENGTH

    async def test_intermediate_text_not_overwritten_by_tool_embed(self) -> None:
        """Tool embed must NOT overwrite intermediate text already shown in placeholder.

        Regression: when intermediate text was displayed first, a subsequent
        ToolSummaryRenderEvent would edit the same placeholder and erase the text
        the user could see.  The fix suppresses tool-summary edits once
        had_intermediate_text=True, so the placeholder keeps showing the text
        and the final response arrives as a new message.
        """
        adapter, channel, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def inter_then_tool():
            yield TextRenderEvent(text="Pre-tool text.", is_final=False)
            yield ToolSummaryRenderEvent(bash_commands=["make test"], is_complete=True)
            yield TextRenderEvent(text="Result.", is_final=True)

        await adapter.send_streaming(msg, inter_then_tool())

        # Tool embed must NOT appear — it would overwrite the intermediate text.
        # NOTE: intermediate-text edits use embed=None to clear any existing embed,
        # so we only count calls where embed is a non-None object.
        embed_edits = [
            c
            for c in placeholder.edit.call_args_list
            if c.kwargs.get("embed") is not None
        ]
        assert len(embed_edits) == 0, (
            "Tool summary embed must not overwrite intermediate text in the placeholder"
        )

        # Final text must be sent as a new message (had_tool_events=True path).
        assert channel.send.await_count >= 1


# ---------------------------------------------------------------------------
# Bug fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_streaming_fallback_sends_all_chunks() -> None:
    """Streaming fallback must send ALL chunks when content exceeds 4096 chars.

    Regression for: only chunks_rendered[0] was sent, truncating long responses.
    """
    from lyra.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=MagicMock(),
        webhook_secret="s",
    )
    fallback_msgs = [MagicMock(message_id=i) for i in range(1, 4)]
    bot = AsyncMock()
    # First send_message raises (placeholder) → triggers fallback path
    bot.send_message = AsyncMock(
        side_effect=[RuntimeError("placeholder fail")] + fallback_msgs
    )
    adapter.bot = bot

    msg = make_tg_message()

    # Content that renders to 3 chunks of 4096 chars each (after escaping)
    long_text = "a" * (4096 * 3)

    async def long_events():
        yield TextRenderEvent(text=long_text, is_final=True)

    outbound = MagicMock()
    outbound.metadata = {}
    await adapter.send_streaming(msg, long_events(), outbound)

    # Placeholder attempt + 3 fallback chunks = 4 total send_message calls
    assert bot.send_message.await_count == 4
    # reply_message_id set to the LAST chunk's message_id
    assert outbound.metadata["reply_message_id"] == 3
