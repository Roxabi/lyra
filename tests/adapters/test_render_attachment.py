"""Tests for render_attachment() on Telegram and Discord adapters (issue #184).

Covers:
- OutboundAttachment construction and frozen enforcement
- TelegramAdapter.render_attachment() dispatches to send_photo/send_video/send_document
- DiscordAdapter.render_attachment() sends as discord.File
- Platform guard (wrong platform → log + return)
- Missing routing key guard (no chat_id/channel_id → log + return)
- Caption forwarding
- reply_to_id override with fallback to inbound message_id
- Topic/thread threading
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters._shared import sanitize_filename, truncate_caption
from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    OutboundAttachment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tg_msg(
    chat_id: int = 42,
    message_id: int = 10,
    topic_id: int | None = None,
    *,
    omit_chat_id: bool = False,
) -> InboundMessage:
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
        trust_level=TrustLevel.TRUSTED,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            **({"chat_id": chat_id} if not omit_chat_id else {}),
            "message_id": message_id,
            "topic_id": topic_id,
            "is_group": False,
        },
    )


def _dc_msg(
    channel_id: int = 99,
    message_id: int = 55,
    thread_id: int | None = None,
    *,
    omit_channel_id: bool = False,
) -> InboundMessage:
    return InboundMessage(
        id=f"discord:dc:user:1:0:{message_id}",
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        trust_level=TrustLevel.TRUSTED,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 1,
            **({"channel_id": channel_id} if not omit_channel_id else {}),
            "message_id": message_id,
            "thread_id": thread_id,
            "channel_type": "text",
        },
    )


def _make_tg_adapter() -> TelegramAdapter:
    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub)
    bot_mock = AsyncMock()
    bot_mock.send_photo = AsyncMock()
    bot_mock.send_video = AsyncMock()
    bot_mock.send_document = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def _make_dc_adapter() -> DiscordAdapter:
    hub = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main")
    return adapter


def _mock_channel() -> MagicMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    return ch


# ---------------------------------------------------------------------------
# OutboundAttachment dataclass
# ---------------------------------------------------------------------------


class TestOutboundAttachment:
    def test_fields(self) -> None:
        att = OutboundAttachment(
            data=b"PNG_DATA",
            type="image",
            mime_type="image/png",
            filename="photo.png",
            caption="A photo",
            reply_to_id="123",
        )
        assert att.data == b"PNG_DATA"
        assert att.type == "image"
        assert att.mime_type == "image/png"
        assert att.filename == "photo.png"
        assert att.caption == "A photo"
        assert att.reply_to_id == "123"

    def test_defaults(self) -> None:
        att = OutboundAttachment(data=b"x", type="file", mime_type="application/pdf")
        assert att.filename is None
        assert att.caption is None
        assert att.reply_to_id is None

    def test_frozen(self) -> None:
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        with pytest.raises(AttributeError):
            att.type = "video"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TelegramAdapter.render_attachment
# ---------------------------------------------------------------------------


class TestTelegramRenderAttachment:
    @pytest.mark.asyncio
    async def test_send_photo(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(data=b"IMG", type="image", mime_type="image/jpeg")
        inbound = _tg_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_photo.assert_awaited_once()
        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["chat_id"] == 42
        assert isinstance(kwargs["photo"], BytesIO)
        assert kwargs["photo"].read() == b"IMG"

    @pytest.mark.asyncio
    async def test_send_video(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(data=b"VID", type="video", mime_type="video/mp4")
        inbound = _tg_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_video.assert_awaited_once()
        kwargs = adapter.bot.send_video.call_args.kwargs
        assert isinstance(kwargs["video"], BytesIO)

    @pytest.mark.asyncio
    async def test_send_document(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(
            data=b"PDF",
            type="document",
            mime_type="application/pdf",
            filename="doc.pdf",
        )
        inbound = _tg_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_document.assert_awaited_once()
        kwargs = adapter.bot.send_document.call_args.kwargs
        buf = kwargs["document"]
        assert isinstance(buf, BytesIO)
        assert buf.name == "doc.pdf"

    @pytest.mark.asyncio
    async def test_send_file_uses_send_document(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(
            data=b"BIN",
            type="file",
            mime_type="application/octet-stream",
        )
        inbound = _tg_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_platform_guard(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _dc_msg()  # wrong platform

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_photo.assert_not_awaited()
        adapter.bot.send_document.assert_not_awaited()
        adapter.bot.send_video.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_chat_id(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _tg_msg(omit_chat_id=True)

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_caption(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", caption="Look at this"
        )
        inbound = _tg_msg()

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["caption"] == "Look at this"

    @pytest.mark.asyncio
    async def test_reply_to_override(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", reply_to_id="200"
        )
        inbound = _tg_msg(message_id=77)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["reply_to_message_id"] == 200

    @pytest.mark.asyncio
    async def test_default_reply_to(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _tg_msg(message_id=77)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["reply_to_message_id"] == 77

    @pytest.mark.asyncio
    async def test_topic_threading(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _tg_msg(topic_id=5)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["message_thread_id"] == 5


# ---------------------------------------------------------------------------
# DiscordAdapter.render_attachment
# ---------------------------------------------------------------------------


class TestDiscordRenderAttachment:
    @pytest.mark.asyncio
    async def test_send_file(self) -> None:
        # Arrange
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"PNG", type="image", mime_type="image/png", filename="shot.png"
        )
        inbound = _dc_msg(channel_id=99, message_id=55)

        # Act
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        # Assert
        ref_msg.reply.assert_awaited_once()
        import discord as _discord

        call_kwargs = ref_msg.reply.call_args.kwargs
        assert isinstance(call_kwargs["file"], _discord.File)
        assert call_kwargs["file"].filename == "shot.png"

    @pytest.mark.asyncio
    async def test_filename_from_mime_type(self) -> None:
        # Arrange
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="document",
            mime_type="application/pdf",
        )
        inbound = _dc_msg()

        # Act
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        # Assert
        call_kwargs = ref_msg.reply.call_args.kwargs
        assert call_kwargs["file"].filename == "attachment.pdf"

    @pytest.mark.asyncio
    async def test_platform_guard(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _tg_msg()  # wrong platform

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_not_awaited()
        channel.fetch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_channel_id(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _dc_msg(omit_channel_id=True)

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_caption(self) -> None:
        # Arrange
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", caption="Check this"
        )
        inbound = _dc_msg()

        # Act
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        # Assert
        call_kwargs = ref_msg.reply.call_args.kwargs
        assert call_kwargs["content"] == "Check this"

    @pytest.mark.asyncio
    async def test_reply_to_override(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)

        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
            reply_to_id="200",
        )
        inbound = _dc_msg(message_id=55)

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.fetch_message.assert_called_with(200)

    @pytest.mark.asyncio
    async def test_no_reply_target_sends_directly(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()

        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
        )
        inbound = InboundMessage(
            id="discord:dc:user:1:0:0",
            platform="discord",
            bot_id="main",
            scope_id="channel:99",
            user_id="dc:user:1",
            user_name="Bob",
            is_mention=False,
            text="hi",
            text_raw="hi",
            trust_level=TrustLevel.TRUSTED,
            timestamp=datetime.now(timezone.utc),
            platform_meta={
                "guild_id": 1,
                "channel_id": 99,
                "message_id": None,
                "thread_id": None,
                "channel_type": "text",
            },
        )

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reply_fallback(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        channel.fetch_message = AsyncMock(side_effect=Exception("not found"))

        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _dc_msg()

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_thread_routing(self) -> None:
        # Arrange
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = _dc_msg(thread_id=777)
        resolve = AsyncMock(return_value=channel)

        # Act
        with patch.object(adapter, "_resolve_channel", resolve):
            await adapter.render_attachment(att, inbound)

        # Assert — _resolve_channel called with thread_id, not channel_id
        resolve.assert_called_with(777)


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    _EXTS = frozenset({"png", "jpg", "pdf"})

    def test_clean_filename_passes_through(self) -> None:
        assert sanitize_filename("photo.png", self._EXTS) == "photo.png"

    def test_path_traversal_stripped(self) -> None:
        result = sanitize_filename("../../etc/passwd.png", self._EXTS)
        assert "/" not in result
        assert ".." not in result or result == "attachment.bin"
        assert result == "passwd.png"

    def test_control_characters_stripped(self) -> None:
        result = sanitize_filename("file\x00name.png", self._EXTS)
        assert "\x00" not in result
        assert result == "filename.png"

    def test_long_name_truncated(self) -> None:
        long_name = "a" * 300 + ".png"
        result = sanitize_filename(long_name, self._EXTS)
        assert len(result) <= 255

    def test_extension_not_in_whitelist_returns_fallback(self) -> None:
        assert sanitize_filename("script.exe", self._EXTS) == "attachment.bin"

    def test_empty_name_returns_fallback(self) -> None:
        assert sanitize_filename("", self._EXTS) == "attachment.bin"

    def test_custom_fallback(self) -> None:
        result = sanitize_filename("bad.exe", self._EXTS, fallback="safe.bin")
        assert result == "safe.bin"


# ---------------------------------------------------------------------------
# truncate_caption
# ---------------------------------------------------------------------------


class TestTruncateCaption:
    def test_none_returns_none(self) -> None:
        assert truncate_caption(None, 100) is None

    def test_empty_returns_none(self) -> None:
        assert truncate_caption("", 100) is None

    def test_within_limit_unchanged(self) -> None:
        assert truncate_caption("hello", 100) == "hello"

    def test_at_limit_unchanged(self) -> None:
        s = "x" * 100
        assert truncate_caption(s, 100) == s

    def test_over_limit_truncated(self) -> None:
        s = "x" * 200
        result = truncate_caption(s, 100)
        assert result is not None
        assert len(result) == 100


# ---------------------------------------------------------------------------
# Integration: traversal filenames + caption truncation boundaries
# ---------------------------------------------------------------------------


class TestTelegramIntegration:
    @pytest.mark.asyncio
    async def test_traversal_filename_sanitized(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(
            data=b"x",
            type="document",
            mime_type="application/pdf",
            filename="../../etc/evil.pdf",
        )
        inbound = _tg_msg()
        await adapter.render_attachment(att, inbound)
        buf = adapter.bot.send_document.call_args.kwargs["document"]
        assert "/" not in buf.name
        assert ".." not in buf.name

    @pytest.mark.asyncio
    async def test_caption_truncated_at_1024(self) -> None:
        adapter = _make_tg_adapter()
        long_caption = "x" * 2000
        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
            caption=long_caption,
        )
        inbound = _tg_msg()
        await adapter.render_attachment(att, inbound)
        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert len(kwargs["caption"]) == 1024

    @pytest.mark.asyncio
    async def test_unknown_mime_fallback(self) -> None:
        adapter = _make_tg_adapter()
        att = OutboundAttachment(
            data=b"x",
            type="file",
            mime_type="application/x-custom",
        )
        inbound = _tg_msg()
        await adapter.render_attachment(att, inbound)
        buf = adapter.bot.send_document.call_args.kwargs["document"]
        assert buf.name == "attachment.bin"


class TestDiscordIntegration:
    @pytest.mark.asyncio
    async def test_traversal_filename_sanitized(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="document",
            mime_type="application/pdf",
            filename="../../etc/evil.pdf",
        )
        inbound = _dc_msg()
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)
        file_obj = ref_msg.reply.call_args.kwargs["file"]
        assert "/" not in file_obj.filename
        assert ".." not in file_obj.filename

    @pytest.mark.asyncio
    async def test_caption_truncated_at_2000(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        long_caption = "x" * 3000
        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
            caption=long_caption,
        )
        inbound = _dc_msg()
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)
        content = ref_msg.reply.call_args.kwargs["content"]
        assert len(content) == 2000

    @pytest.mark.asyncio
    async def test_unknown_mime_fallback(self) -> None:
        adapter = _make_dc_adapter()
        channel = _mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="file",
            mime_type="application/x-custom",
        )
        inbound = _dc_msg()
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)
        file_obj = ref_msg.reply.call_args.kwargs["file"]
        assert file_obj.filename == "attachment.bin"
