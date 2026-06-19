"""T16b — TelegramAdapter._make_emitter smoke test (#1279, Slice 4+5).

Verifies that TelegramAdapter._make_emitter() constructs a real OutboundEmitter
composed with TelegramFormatter and that send_streaming() runs it end-to-end with
platform-correct rich-message kwargs at each call site.

SC-10 smoke acceptance criteria:
  - send_streaming() completes without raising
  - bot.send_rich_message called at least once (placeholder send)
  - First placeholder call includes reply_to_message_id
  - Final edit call (edit_message_text) includes rich_message
  - OutboundMessage.metadata["reply_message_id"] is populated
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.telegram import TelegramAdapter
from tests.adapters.conftest import wire_telegram_rich_bot
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import InboundMessage, OutboundMessage, TelegramMeta
from factory.core.messaging.render_events import (
    RunErrorRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_telegram_rich_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke tests target persisted rich messages, not private-chat drafts."""
    monkeypatch.setenv("FACTORY_TELEGRAM_RICH_DRAFTS", "0")


def _make_tg_adapter_with_bot() -> tuple[TelegramAdapter, AsyncMock]:
    """Build a TelegramAdapter with a fully mocked aiogram bot."""
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token",
        inbound_bus=MagicMock(),
    )
    bot = AsyncMock()
    wire_telegram_rich_bot(bot, message_id=100)
    adapter.bot = bot
    return adapter, bot


def _make_tg_inbound(chat_id: int = 42, message_id: int = 10) -> InboundMessage:
    return InboundMessage(
        id=f"telegram:tg:user:1:0:{message_id}",
        platform="telegram",
        bot_id="main",
        scope_id=f"chat:{chat_id}",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(
            chat_id=chat_id,
            message_id=message_id,
            topic_id=None,
            is_group=False,
        ),
        trust_level=TrustLevel.TRUSTED,
    )


async def _three_chunk_events():
    """Yield a minimal 3-delta text stream."""
    yield TextStartRenderEvent(message_id="m1")
    yield TextDeltaRenderEvent(delta="Hello", message_id="m1")
    yield TextDeltaRenderEvent(delta=" world", message_id="m1")
    yield TextEndRenderEvent(message_id="m1")


async def _soft_error_only_events():
    """Soft LLM error with no text deltas (ADR-089 streaming path)."""
    yield RunErrorRenderEvent(run_id="r1", message="You've hit your weekly limit")


# ---------------------------------------------------------------------------
# T16b — _make_emitter returns a real OutboundEmitter
# ---------------------------------------------------------------------------


class TestTelegramMakeEmitter:
    def test_make_emitter_returns_outbound_emitter(self) -> None:
        """_make_emitter() must return an OutboundEmitter instance."""
        # Arrange
        from factory.outbound.emitter import OutboundEmitter

        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound()
        outbound = OutboundMessage.from_text("")

        # Act
        emitter = adapter._make_emitter(original_msg, outbound)

        # Assert
        assert isinstance(emitter, OutboundEmitter)

    def test_make_emitter_with_none_outbound(self) -> None:
        """_make_emitter() must not raise when outbound=None."""
        from factory.outbound.emitter import OutboundEmitter

        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound()

        emitter = adapter._make_emitter(original_msg, None)

        assert isinstance(emitter, OutboundEmitter)

    def test_make_emitter_non_telegram_falls_back(self) -> None:
        """_make_emitter() with non-telegram inbound falls back to legacy callbacks."""
        from factory.core.auth.trust import TrustLevel
        from factory.core.messaging.message import DiscordMeta
        from factory.outbound.emitter import OutboundEmitter

        adapter, _ = _make_tg_adapter_with_bot()
        discord_msg = InboundMessage(
            id="discord:dc:user:1:0:1",
            platform="discord",
            bot_id="main",
            scope_id="channel:99",
            user_id="dc:user:1",
            user_name="Bob",
            is_mention=False,
            text="hi",
            text_raw="hi",
            timestamp=datetime.now(timezone.utc),
            platform_meta=DiscordMeta(
                guild_id=1,
                channel_id=99,
                message_id=55,
                thread_id=None,
                channel_type="text",
            ),
            trust_level=TrustLevel.TRUSTED,
        )

        # Should not raise — falls back to noop callbacks path
        emitter = adapter._make_emitter(discord_msg, None)
        assert isinstance(emitter, OutboundEmitter)


# ---------------------------------------------------------------------------
# SC-10 — send_streaming() end-to-end: placeholder → edit chain
# ---------------------------------------------------------------------------


class TestTelegramSendStreamingSmoke:
    async def test_send_streaming_calls_bot_send_message(self) -> None:
        """send_streaming() must call bot.send_rich_message at least once (placeholder)."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — placeholder was sent
        bot.send_rich_message.assert_awaited()
        assert bot.send_rich_message.await_count >= 1

    async def test_send_streaming_edit_has_parse_mode_markdownv2(self) -> None:
        """Streaming edit_message_text calls must include rich_message payload."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — at least one edit uses rich_message
        bot.edit_message_text.assert_awaited()
        found_rich = any(
            call.kwargs.get("rich_message") is not None
            for call in bot.edit_message_text.call_args_list
        )
        assert found_rich, "Expected at least one edit_message_text with rich_message"

    async def test_send_streaming_placeholder_has_reply_to_message_id(self) -> None:
        """Placeholder send_rich_message must include reply_to_message_id=message_id."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — reply_to_message_id in first call
        first_call = bot.send_rich_message.call_args_list[0]
        kwargs = first_call.kwargs
        assert kwargs.get("reply_to_message_id") == 10, (
            f"Expected reply_to_message_id=10, got: {kwargs}"
        )

    async def test_send_streaming_final_edit_uses_markdownv2(self) -> None:
        """Final delivery edit_message_text (last call) must use rich_message."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — the final (last) edit_message_text call carries rich_message
        bot.edit_message_text.assert_awaited()
        last_call = bot.edit_message_text.call_args_list[-1]
        assert last_call.kwargs.get("rich_message") is not None, (
            "Expected final edit_message_text to use rich_message, "
            f"got kwargs: {last_call.kwargs}"
        )

    async def test_send_streaming_populates_reply_message_id_in_metadata(
        self,
    ) -> None:
        """send_streaming() must populate outbound.metadata['reply_message_id']."""
        # Arrange
        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert
        assert "reply_message_id" in outbound.metadata, (
            "outbound.metadata must contain 'reply_message_id' after send_streaming"
        )
        assert outbound.metadata["reply_message_id"] == 100

    async def test_send_streaming_placeholder_before_edit(self) -> None:
        """Placeholder send_rich_message must happen before any edit_message_text call."""
        # Arrange
        call_order: list[str] = []

        adapter, bot = _make_tg_adapter_with_bot()

        async def record_send(*args, **kwargs):
            call_order.append("send")
            return SimpleNamespace(message_id=100)

        async def record_edit(*args, **kwargs):
            call_order.append("edit")

        bot.send_rich_message = AsyncMock(side_effect=record_send)
        bot.edit_message_text = AsyncMock(side_effect=record_edit)

        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — send happened before any edit
        assert "send" in call_order, "bot.send_rich_message must be called"
        if "edit" in call_order:
            first_send = call_order.index("send")
            first_edit = call_order.index("edit")
            assert first_send < first_edit, "Placeholder must be sent before any edit"

    async def test_send_streaming_completes_without_raising(self) -> None:
        """send_streaming() must not raise for a normal 3-chunk stream."""
        # Arrange
        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act / Assert — no exception
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

    async def test_send_streaming_with_none_outbound_does_not_raise(self) -> None:
        """send_streaming(outbound=None) must not raise."""
        # Arrange
        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)

        # Act / Assert
        await adapter.send_streaming(original_msg, _three_chunk_events(), outbound=None)

    async def test_send_streaming_text_content_in_final_edit(self) -> None:
        """The final edit must contain the accumulated text ('Hello world')."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — last edit_message_text call has the accumulated content
        bot.edit_message_text.assert_awaited()
        last_call = bot.edit_message_text.call_args_list[-1]
        rich = last_call.kwargs.get("rich_message")
        text_arg = rich.markdown if rich is not None else last_call.kwargs.get("text")
        assert text_arg is not None, "edit_message_text must receive content"
        assert "Hello" in text_arg and "world" in text_arg

    async def test_send_streaming_soft_error_displays_run_error_message(self) -> None:
        """ADR-089: soft error only shows curated message on Telegram."""
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        await adapter.send_streaming(
            original_msg, _soft_error_only_events(), outbound=outbound
        )

        bot.edit_message_text.assert_awaited()
        last_call = bot.edit_message_text.call_args_list[-1]
        rich = last_call.kwargs.get("rich_message")
        text_arg = rich.markdown if rich is not None else last_call.kwargs.get("text")
        assert text_arg is not None
        assert "weekly limit" in text_arg
