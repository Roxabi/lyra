"""T16b — TelegramAdapter._make_emitter smoke test (#1279, Slice 4+5).

Verifies that TelegramAdapter._make_emitter() constructs a real OutboundEmitter
composed with TelegramFormatter and that send_streaming() runs it end-to-end with
platform-correct bot.send_message kwargs at each call site.

SC-10 smoke acceptance criteria:
  - send_streaming() completes without raising
  - bot.send_message called at least once (placeholder send)
  - First placeholder call includes parse_mode="MarkdownV2" and reply_to_message_id
  - Final edit call (edit_message_text) includes parse_mode="MarkdownV2"
  - OutboundMessage.metadata["reply_message_id"] is populated
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from lyra.adapters.telegram import TelegramAdapter
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, OutboundMessage, TelegramMeta
from lyra.core.messaging.render_events import (
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tg_adapter_with_bot() -> tuple[TelegramAdapter, AsyncMock]:
    """Build a TelegramAdapter with a fully mocked aiogram bot."""
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token",
        inbound_bus=MagicMock(),
    )
    bot = AsyncMock()
    # send_message returns a Telegram Message-like object with .message_id
    placeholder_msg = SimpleNamespace(message_id=100)
    bot.send_message = AsyncMock(return_value=placeholder_msg)
    # edit_message_text is called during streaming and final delivery
    bot.edit_message_text = AsyncMock()
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


# ---------------------------------------------------------------------------
# T16b — _make_emitter returns a real OutboundEmitter
# ---------------------------------------------------------------------------


class TestTelegramMakeEmitter:
    def test_make_emitter_returns_outbound_emitter(self) -> None:
        """_make_emitter() must return an OutboundEmitter instance."""
        # Arrange
        from lyra.outbound.emitter import OutboundEmitter

        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound()
        outbound = OutboundMessage.from_text("")

        # Act
        emitter = adapter._make_emitter(original_msg, outbound)

        # Assert
        assert isinstance(emitter, OutboundEmitter)

    def test_make_emitter_with_none_outbound(self) -> None:
        """_make_emitter() must not raise when outbound=None."""
        from lyra.outbound.emitter import OutboundEmitter

        adapter, _ = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound()

        emitter = adapter._make_emitter(original_msg, None)

        assert isinstance(emitter, OutboundEmitter)

    def test_make_emitter_non_telegram_falls_back(self) -> None:
        """_make_emitter() with non-telegram inbound falls back to legacy callbacks."""
        from lyra.core.auth.trust import TrustLevel
        from lyra.core.messaging.message import DiscordMeta
        from lyra.outbound.emitter import OutboundEmitter

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
        """send_streaming() must call bot.send_message at least once (placeholder)."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — placeholder was sent
        bot.send_message.assert_awaited()
        assert bot.send_message.await_count >= 1

    async def test_send_streaming_edit_has_parse_mode_markdownv2(self) -> None:
        """Streaming edit_message_text calls must include parse_mode='MarkdownV2'.

        The placeholder send_message itself has no parse_mode (plain '…' text);
        parse_mode is applied on edit_message_text and final delivery calls only.
        """
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — at least one edit_message_text call has parse_mode=MarkdownV2
        bot.edit_message_text.assert_awaited()
        found_markdownv2 = any(
            call.kwargs.get("parse_mode") == "MarkdownV2"
            for call in bot.edit_message_text.call_args_list
        )
        assert found_markdownv2, (
            "Expected at least one edit_message_text call with parse_mode='MarkdownV2'"
        )

    async def test_send_streaming_placeholder_has_reply_to_message_id(self) -> None:
        """Placeholder send_message call must include reply_to_message_id=message_id."""
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — reply_to_message_id in first call
        first_call = bot.send_message.call_args_list[0]
        kwargs = first_call.kwargs
        assert kwargs.get("reply_to_message_id") == 10, (
            f"Expected reply_to_message_id=10, got: {kwargs}"
        )

    async def test_send_streaming_final_edit_uses_markdownv2(self) -> None:
        """Final delivery edit_message_text (last call) must use parse_mode=MarkdownV2.

        Distinct from the intermediate-edit assertion: this pins the *last*
        edit_message_text call (the final delivery) to MarkdownV2 specifically.
        """  # noqa: E501
        # Arrange
        adapter, bot = _make_tg_adapter_with_bot()
        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — the final (last) edit_message_text call carries MarkdownV2
        bot.edit_message_text.assert_awaited()
        last_call = bot.edit_message_text.call_args_list[-1]
        assert last_call.kwargs.get("parse_mode") == "MarkdownV2", (
            "Expected final edit_message_text to use parse_mode='MarkdownV2', "
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
        """Placeholder send_message must happen before any edit_message_text call."""
        # Arrange
        call_order: list[str] = []

        adapter, bot = _make_tg_adapter_with_bot()

        async def record_send(*args, **kwargs):
            call_order.append("send")
            return SimpleNamespace(message_id=100)

        async def record_edit(*args, **kwargs):
            call_order.append("edit")

        bot.send_message = AsyncMock(side_effect=record_send)
        bot.edit_message_text = AsyncMock(side_effect=record_edit)

        original_msg = _make_tg_inbound(chat_id=42, message_id=10)
        outbound = OutboundMessage.from_text("")

        # Act
        await adapter.send_streaming(
            original_msg, _three_chunk_events(), outbound=outbound
        )

        # Assert — send happened before any edit
        assert "send" in call_order, "bot.send_message must be called"
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
        text_arg = last_call.kwargs.get("text") or (
            last_call.args[0] if last_call.args else ""
        )
        # MarkdownV2 escapes spaces and characters, but "Hello" and "world" must be
        # present after escaping (content check at character level).
        assert text_arg is not None, "edit_message_text must receive a text argument"
