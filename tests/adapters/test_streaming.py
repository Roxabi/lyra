"""Tests for adapter send_streaming: Telegram + Discord edit-in-place with debounce."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.message import InboundMessage, OutboundMessage

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


async def quick_chunks():
    """Yield chunks quickly — no debounce threshold crossed."""
    yield "Hello"
    yield " world"
    yield "!"


async def error_chunks():
    """Yield some chunks then raise an error."""
    yield "partial"
    raise RuntimeError("stream died")


# ---------------------------------------------------------------------------
# Telegram streaming
# ---------------------------------------------------------------------------


class TestTelegramStreaming:
    def _make_adapter(self):
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(
            bot_id="main",
            token="fake-token",
            hub=hub,
            bot_username="lyra_bot",
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

        await adapter.send_streaming(msg, quick_chunks())

        # Placeholder sent
        bot.send_message.assert_awaited_once()
        # Final edit called with full text (MarkdownV2-escaped)
        last_edit = bot.edit_message_text.call_args
        assert last_edit.kwargs["text"] == "Hello world\\!"
        assert last_edit.kwargs.get("parse_mode") == "MarkdownV2"

    async def test_debounce_limits_edits(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        # With quick chunks (no delay), edits are debounced — only final edit
        await adapter.send_streaming(msg, quick_chunks())

        # Final edit always happens, but intermediate edits are debounced
        # Quick chunks arrive within debounce window, so only final edit
        assert bot.edit_message_text.await_count >= 1

    async def test_placeholder_failure_falls_back(self) -> None:
        adapter, bot = self._make_adapter()
        # First call (placeholder) fails, second call (fallback send) succeeds
        bot.send_message = AsyncMock(side_effect=[RuntimeError("network"), MagicMock()])
        msg = make_tg_message()

        await adapter.send_streaming(msg, quick_chunks())

        # Should fall back to regular send with full accumulated text
        assert bot.send_message.await_count == 2
        fallback_call = bot.send_message.call_args_list[1]
        assert fallback_call.kwargs["text"] == "Hello world\\!"
        assert fallback_call.kwargs.get("parse_mode") == "MarkdownV2"

    async def test_stores_reply_message_id_in_outbound(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(msg, quick_chunks(), outbound)

        assert outbound.metadata["reply_message_id"] == 999

    async def test_no_outbound_still_works(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        await adapter.send_streaming(msg, quick_chunks())

        bot.send_message.assert_awaited_once()

    async def test_mid_stream_error_stores_reply_message_id(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()
        outbound = OutboundMessage.from_text("")

        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_chunks(), outbound)

        # reply_message_id set before error (placeholder succeeded)
        assert outbound.metadata["reply_message_id"] == 999

    async def test_mid_stream_error_appends_interrupted(self) -> None:
        adapter, bot = self._make_adapter()
        msg = make_tg_message()

        # send_streaming now re-raises after the final edit so OutboundDispatcher
        # can record CB failure
        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_chunks())

        last_edit = bot.edit_message_text.call_args
        assert "\\[response interrupted\\]" in last_edit.kwargs["text"]
        assert "partial" in last_edit.kwargs["text"]
        assert last_edit.kwargs.get("parse_mode") == "MarkdownV2"

    async def test_placeholder_failure_writes_fallback_id_to_outbound(self) -> None:
        adapter, bot = self._make_adapter()
        fallback_msg = MagicMock()
        fallback_msg.message_id = 1001
        bot.send_message = AsyncMock(
            side_effect=[RuntimeError("network"), fallback_msg]
        )
        msg = make_tg_message()
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(msg, quick_chunks(), outbound)

        assert outbound.metadata["reply_message_id"] == 1001


# ---------------------------------------------------------------------------
# Discord streaming
# ---------------------------------------------------------------------------


class TestDiscordStreaming:
    def _make_adapter(self):
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main")

        mock_placeholder = AsyncMock()
        mock_placeholder.edit = AsyncMock()

        mock_channel = MagicMock()
        mock_channel.send = AsyncMock(return_value=mock_placeholder)

        adapter.get_channel = MagicMock(return_value=mock_channel)
        return adapter, mock_channel, mock_placeholder

    async def test_sends_placeholder_then_edits(self) -> None:
        adapter, channel, placeholder = self._make_adapter()
        msg = make_dc_message()

        await adapter.send_streaming(msg, quick_chunks())

        channel.send.assert_awaited_once_with("\u2026")
        last_edit = placeholder.edit.call_args
        assert last_edit.kwargs["content"] == "Hello world!"

    async def test_mid_stream_error_appends_interrupted(self) -> None:
        adapter, channel, placeholder = self._make_adapter()
        msg = make_dc_message()

        # send_streaming now re-raises after the final edit so OutboundDispatcher
        # can record CB failure
        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_chunks())

        last_edit = placeholder.edit.call_args
        assert "[response interrupted]" in last_edit.kwargs["content"]

    async def test_stores_reply_message_id_in_outbound(self) -> None:
        adapter, channel, placeholder = self._make_adapter()
        placeholder.id = 777
        msg = make_dc_message()
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(msg, quick_chunks(), outbound)

        assert outbound.metadata["reply_message_id"] == 777

    async def test_mid_stream_error_stores_reply_message_id(self) -> None:
        adapter, channel, placeholder = self._make_adapter()
        placeholder.id = 777
        msg = make_dc_message()
        outbound = OutboundMessage.from_text("")

        with pytest.raises(RuntimeError, match="stream died"):
            await adapter.send_streaming(msg, error_chunks(), outbound)

        assert outbound.metadata["reply_message_id"] == 777

    async def test_truncates_at_discord_max(self) -> None:
        adapter, channel, placeholder = self._make_adapter()
        msg = make_dc_message()

        async def long_chunks():
            yield "x" * 3000

        await adapter.send_streaming(msg, long_chunks())

        last_edit = placeholder.edit.call_args
        assert len(last_edit.kwargs["content"]) <= 2000
