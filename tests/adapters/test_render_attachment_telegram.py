"""Tests for render_attachment() on the Telegram adapter (issue #184).

Covers:
- OutboundAttachment construction and frozen enforcement
- TelegramAdapter.render_attachment() dispatches to send_photo/send_video/send_document
- sanitize_filename utility
- truncate_caption utility
- Platform guard (wrong platform -> log + return)
- Missing routing key guard (no chat_id -> log + return)
- Caption forwarding
- reply_to_id override with fallback to inbound message_id
- Topic/thread threading
- Integration: traversal filenames + caption truncation boundaries
"""

from __future__ import annotations

from io import BytesIO

import pytest

from lyra.adapters.shared._shared import sanitize_filename, truncate_caption
from lyra.core.messaging.message import OutboundAttachment

from .conftest import make_dc_attach_msg, make_tg_attach_adapter, make_tg_attach_msg

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
            setattr(att, "type", "video")


# ---------------------------------------------------------------------------
# TelegramAdapter.render_attachment
# ---------------------------------------------------------------------------


class TestTelegramRenderAttachment:
    @pytest.mark.asyncio
    async def test_send_photo(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(data=b"IMG", type="image", mime_type="image/jpeg")
        inbound = make_tg_attach_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_photo.assert_awaited_once()
        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["chat_id"] == 42
        assert isinstance(kwargs["photo"], BytesIO)
        assert kwargs["photo"].read() == b"IMG"

    @pytest.mark.asyncio
    async def test_send_video(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(data=b"VID", type="video", mime_type="video/mp4")
        inbound = make_tg_attach_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_video.assert_awaited_once()
        kwargs = adapter.bot.send_video.call_args.kwargs
        assert isinstance(kwargs["video"], BytesIO)

    @pytest.mark.asyncio
    async def test_send_document(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"PDF",
            type="document",
            mime_type="application/pdf",
            filename="doc.pdf",
        )
        inbound = make_tg_attach_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_document.assert_awaited_once()
        kwargs = adapter.bot.send_document.call_args.kwargs
        buf = kwargs["document"]
        assert isinstance(buf, BytesIO)
        assert buf.name == "doc.pdf"

    @pytest.mark.asyncio
    async def test_send_file_uses_send_document(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"BIN",
            type="file",
            mime_type="application/octet-stream",
        )
        inbound = make_tg_attach_msg()

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_platform_guard(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_dc_attach_msg()  # wrong platform

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_photo.assert_not_awaited()
        adapter.bot.send_document.assert_not_awaited()
        adapter.bot.send_video.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_chat_id(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_tg_attach_msg(omit_chat_id=True)

        await adapter.render_attachment(att, inbound)

        adapter.bot.send_photo.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_caption(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", caption="Look at this"
        )
        inbound = make_tg_attach_msg()

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["caption"] == "Look at this"

    @pytest.mark.asyncio
    async def test_reply_to_override(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", reply_to_id="200"
        )
        inbound = make_tg_attach_msg(message_id=77)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["reply_to_message_id"] == 200

    @pytest.mark.asyncio
    async def test_default_reply_to(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_tg_attach_msg(message_id=77)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["reply_to_message_id"] == 77

    @pytest.mark.asyncio
    async def test_topic_threading(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(data=b"x", type="image", mime_type="image/png")
        inbound = make_tg_attach_msg(topic_id=5)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["message_thread_id"] == 5

    @pytest.mark.asyncio
    async def test_invalid_reply_to_id_falls_back(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"x", type="image", mime_type="image/png", reply_to_id="not-a-number"
        )
        inbound = make_tg_attach_msg(message_id=77)

        await adapter.render_attachment(att, inbound)

        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert kwargs["reply_to_message_id"] == 77


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
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"x",
            type="document",
            mime_type="application/pdf",
            filename="../../etc/evil.pdf",
        )
        inbound = make_tg_attach_msg()
        await adapter.render_attachment(att, inbound)
        buf = adapter.bot.send_document.call_args.kwargs["document"]
        assert "/" not in buf.name
        assert ".." not in buf.name

    @pytest.mark.asyncio
    async def test_caption_truncated_at_1024(self) -> None:
        adapter = make_tg_attach_adapter()
        long_caption = "x" * 2000
        att = OutboundAttachment(
            data=b"x",
            type="image",
            mime_type="image/png",
            caption=long_caption,
        )
        inbound = make_tg_attach_msg()
        await adapter.render_attachment(att, inbound)
        kwargs = adapter.bot.send_photo.call_args.kwargs
        assert len(kwargs["caption"]) == 1024

    @pytest.mark.asyncio
    async def test_unknown_mime_fallback(self) -> None:
        adapter = make_tg_attach_adapter()
        att = OutboundAttachment(
            data=b"x",
            type="file",
            mime_type="application/x-custom",
        )
        inbound = make_tg_attach_msg()
        await adapter.render_attachment(att, inbound)
        buf = adapter.bot.send_document.call_args.kwargs["document"]
        assert buf.name == "attachment.bin"
