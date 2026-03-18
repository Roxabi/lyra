"""Tests for render_attachment() on the Discord adapter (issue #184).

Covers:
- DiscordAdapter.render_attachment() sends as discord.File
- Platform guard (wrong platform -> log + return)
- Missing routing key guard (no channel_id -> log + return)
- Caption forwarding
- reply_to_id override with fallback to inbound message_id
- Thread routing
- Integration: traversal filenames + caption truncation boundaries
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from lyra.core.message import InboundMessage, OutboundAttachment
from lyra.core.trust import TrustLevel

from .conftest import (
    make_dc_attach_adapter,
    make_dc_attach_msg,
    make_tg_attach_msg,
    mock_channel,
)

# ---------------------------------------------------------------------------
# DiscordAdapter.render_attachment
# ---------------------------------------------------------------------------


class TestDiscordRenderAttachment:
    @pytest.mark.asyncio
    async def test_send_file(self) -> None:
        # Arrange
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"PNG", type="image", mime_type="image/png", filename="shot.png"
        )
        inbound = make_dc_attach_msg(channel_id=99, message_id=55)

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
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="document",
            mime_type="application/pdf",
        )
        inbound = make_dc_attach_msg()

        # Act
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        # Assert
        call_kwargs = ref_msg.reply.call_args.kwargs
        assert call_kwargs["file"].filename == "attachment.pdf"

    @pytest.mark.asyncio
    async def test_platform_guard(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_tg_attach_msg()  # wrong platform

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_not_awaited()
        channel.fetch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_channel_id(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_dc_attach_msg(omit_channel_id=True)

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_caption(self) -> None:
        # Arrange
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", caption="Check this"
        )
        inbound = make_dc_attach_msg()

        # Act
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        # Assert
        call_kwargs = ref_msg.reply.call_args.kwargs
        assert call_kwargs["content"] == "Check this"

    @pytest.mark.asyncio
    async def test_reply_to_override(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)

        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
            reply_to_id="200",
        )
        inbound = make_dc_attach_msg(message_id=55)

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.fetch_message.assert_called_with(200)

    @pytest.mark.asyncio
    async def test_no_reply_target_sends_directly(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()

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
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        channel.fetch_message = AsyncMock(side_effect=Exception("not found"))

        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_dc_attach_msg()

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        channel.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_thread_routing(self) -> None:
        # Arrange
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_dc_attach_msg(thread_id=777)
        resolve = AsyncMock(return_value=channel)

        # Act
        with patch.object(adapter, "_resolve_channel", resolve):
            await adapter.render_attachment(att, inbound)

        # Assert — _resolve_channel called with thread_id, not channel_id
        resolve.assert_called_with(777)

    @pytest.mark.asyncio
    async def test_invalid_reply_to_id_falls_back(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
            reply_to_id="not-a-number",
        )
        inbound = make_dc_attach_msg(message_id=55)

        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)

        # Invalid reply_to_id -> falls back to inbound message_id
        channel.fetch_message.assert_called_with(55)


# ---------------------------------------------------------------------------
# Integration: traversal filenames + caption truncation boundaries
# ---------------------------------------------------------------------------


class TestDiscordIntegration:
    @pytest.mark.asyncio
    async def test_traversal_filename_sanitized(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="document",
            mime_type="application/pdf",
            filename="../../etc/evil.pdf",
        )
        inbound = make_dc_attach_msg()
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)
        file_obj = ref_msg.reply.call_args.kwargs["file"]
        assert "/" not in file_obj.filename
        assert ".." not in file_obj.filename

    @pytest.mark.asyncio
    async def test_caption_truncated_at_2000(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
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
        inbound = make_dc_attach_msg()
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)
        content = ref_msg.reply.call_args.kwargs["content"]
        assert len(content) == 2000

    @pytest.mark.asyncio
    async def test_unknown_mime_fallback(self) -> None:
        adapter = make_dc_attach_adapter()
        channel = mock_channel()
        ref_msg = AsyncMock()
        ref_msg.reply = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=ref_msg)
        att = OutboundAttachment(
            data=b"x",
            type="file",
            mime_type="application/x-custom",
        )
        inbound = make_dc_attach_msg()
        with patch.object(adapter, "get_channel", return_value=channel):
            await adapter.render_attachment(att, inbound)
        file_obj = ref_msg.reply.call_args.kwargs["file"]
        assert file_obj.filename == "attachment.bin"
