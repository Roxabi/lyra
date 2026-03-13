"""Tests for outbound attachment handling in Telegram and Discord adapters (#143)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.telegram import _ALLOW_ALL, TelegramAdapter
from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
    OutboundMessage,
    Platform,
)


def _make_inbound_telegram(chat_id: int = 123) -> InboundMessage:
    return InboundMessage(
        id="telegram:tg:user:42:1234:99",
        platform=Platform.TELEGRAM.value,
        bot_id="main",
        scope_id=f"chat:{chat_id}",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        trust_level=TrustLevel.TRUSTED,
        platform_meta={"chat_id": chat_id, "topic_id": None, "message_id": 99},
    )


def _make_inbound_discord(channel_id: int = 456) -> InboundMessage:
    return InboundMessage(
        id="discord:dc:user:42:1234:99",
        platform=Platform.DISCORD.value,
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:42",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 1,
            "channel_id": channel_id,
            "message_id": 99,
            "thread_id": None,
            "channel_type": "text",
        },
    )


# ── Telegram ──────────────────────────────────────────────────────────────


class TestTelegramOutboundAttachments:
    @pytest.fixture
    def adapter(self) -> TelegramAdapter:
        hub = MagicMock()
        a = TelegramAdapter(
            bot_id="main", token="test-token", hub=hub, auth=_ALLOW_ALL
        )
        a.bot = AsyncMock()
        a.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=100)
        )
        a.bot.send_photo = AsyncMock()
        a.bot.send_video = AsyncMock()
        a.bot.send_document = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_send_image_attachment(self, adapter: TelegramAdapter) -> None:
        """Image attachment dispatches to send_photo."""
        att = OutboundAttachment(
            bytes_or_path=b"fake-png",
            file_name="photo.png",
            mime_type="image/png",
            caption="A photo",
        )
        outbound = OutboundMessage(content=["Check this out"], attachments=[att])
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        adapter.bot.send_photo.assert_awaited_once()
        call_kwargs = adapter.bot.send_photo.call_args[1]
        assert call_kwargs["chat_id"] == 123
        assert call_kwargs["caption"] == "A photo"

    @pytest.mark.asyncio
    async def test_send_video_attachment(self, adapter: TelegramAdapter) -> None:
        """Video attachment dispatches to send_video."""
        att = OutboundAttachment(
            bytes_or_path=b"fake-mp4",
            file_name="clip.mp4",
            mime_type="video/mp4",
        )
        outbound = OutboundMessage(content=["Watch this"], attachments=[att])
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        adapter.bot.send_video.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_document_attachment(self, adapter: TelegramAdapter) -> None:
        """Non-image/video attachment dispatches to send_document."""
        att = OutboundAttachment(
            bytes_or_path=b"fake-pdf",
            file_name="report.pdf",
            mime_type="application/pdf",
        )
        outbound = OutboundMessage(content=["Here's the report"], attachments=[att])
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        adapter.bot.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_no_attachments(self, adapter: TelegramAdapter) -> None:
        """No attachments → no send_photo/video/document calls."""
        outbound = OutboundMessage.from_text("Just text")
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        adapter.bot.send_photo.assert_not_awaited()
        adapter.bot.send_video.assert_not_awaited()
        adapter.bot.send_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_multiple_attachments(self, adapter: TelegramAdapter) -> None:
        """Multiple attachments are each sent separately."""
        atts = [
            OutboundAttachment(b"img", "a.png", "image/png"),
            OutboundAttachment(b"doc", "b.pdf", "application/pdf"),
        ]
        outbound = OutboundMessage(content=["Files"], attachments=atts)
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        assert adapter.bot.send_photo.await_count == 1
        assert adapter.bot.send_document.await_count == 1

    @pytest.mark.asyncio
    async def test_send_attachment_from_path(
        self, adapter: TelegramAdapter, tmp_path
    ) -> None:
        """Path-based attachment reads file and dispatches correctly."""
        f = tmp_path / "report.pdf"
        f.write_bytes(b"pdf-content")
        att = OutboundAttachment(
            bytes_or_path=str(f),
            file_name="report.pdf",
            mime_type="application/pdf",
        )
        outbound = OutboundMessage(content=["Here"], attachments=[att])
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        adapter.bot.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_attachment_missing_path(
        self, adapter: TelegramAdapter
    ) -> None:
        """Missing file path logs error but does not crash send()."""
        att = OutboundAttachment(
            bytes_or_path="/nonexistent/file.png",
            file_name="ghost.png",
            mime_type="image/png",
        )
        outbound = OutboundMessage(content=["Here"], attachments=[att])
        inbound = _make_inbound_telegram()

        # Should not raise — error isolation catches the OSError
        await adapter.send(inbound, outbound)

        adapter.bot.send_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_caption_truncated_to_1024(
        self, adapter: TelegramAdapter
    ) -> None:
        """Telegram captions are truncated to 1024 characters."""
        long_caption = "x" * 2000
        att = OutboundAttachment(
            bytes_or_path=b"data",
            file_name="f.pdf",
            mime_type="application/pdf",
            caption=long_caption,
        )
        outbound = OutboundMessage(content=[""], attachments=[att])
        inbound = _make_inbound_telegram()

        await adapter.send(inbound, outbound)

        call_kwargs = adapter.bot.send_document.call_args[1]
        assert len(call_kwargs["caption"]) == 1024


# ── Discord ───────────────────────────────────────────────────────────────


class TestDiscordOutboundAttachments:
    @pytest.fixture
    def adapter(self):  # type: ignore[override]
        from lyra.adapters.discord import _ALLOW_ALL, DiscordAdapter

        hub = MagicMock()
        a = DiscordAdapter(hub=hub, auth=_ALLOW_ALL)
        a._bot_user = SimpleNamespace(id=999)
        return a

    @pytest.mark.asyncio
    async def test_send_attachment(self, adapter: Any) -> None:
        """Attachment is sent as discord.File via messageable.send()."""
        att = OutboundAttachment(
            bytes_or_path=b"fake-png",
            file_name="photo.png",
            mime_type="image/png",
            caption="A photo",
        )
        outbound = OutboundMessage(content=["Check this"], attachments=[att])
        inbound = _make_inbound_discord()

        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock(return_value=SimpleNamespace(id=200))
        adapter.get_channel = MagicMock(return_value=mock_channel)

        await adapter.send(inbound, outbound)

        # Text message + attachment = 2 send calls
        assert mock_channel.send.await_count == 2
        # Second call is the attachment
        att_call = mock_channel.send.call_args_list[1]
        assert att_call[1]["content"] == "A photo"
        assert att_call[1]["file"] is not None

    @pytest.mark.asyncio
    async def test_send_no_attachments(self, adapter: Any) -> None:
        """No attachments → single send call for text only."""
        outbound = OutboundMessage.from_text("Just text")
        inbound = _make_inbound_discord()

        mock_channel = AsyncMock()
        mock_channel.send = AsyncMock(return_value=SimpleNamespace(id=200))
        adapter.get_channel = MagicMock(return_value=mock_channel)

        await adapter.send(inbound, outbound)

        assert mock_channel.send.await_count == 1
