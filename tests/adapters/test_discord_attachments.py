"""Tests for DiscordAdapter inbound attachment extraction (#183)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord


class TestDiscordAttachments:
    """DiscordAdapter.normalize() extracts non-audio attachments."""

    def _make_adapter(self):
        from lyra.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(
            bot_id="main",
            inbound_bus=MagicMock(),
            inbound_audio_bus=MagicMock(),
            intents=discord.Intents.none(),
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)
        return adapter

    def _make_msg(self, *, attachments=None, content="hello"):
        return SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42,
                name="Alice",
                display_name="Alice",
                bot=False,
            ),
            content=content,
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            attachments=attachments,
        )

    def test_normalize_image_attachment(self) -> None:
        """Image attachment → type='image', CDN URL, mime_type."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="image/png",
            url="https://cdn.discord.com/img.png",
            filename="img.png",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.url_or_path_or_bytes == "https://cdn.discord.com/img.png"
        assert a.mime_type == "image/png"
        assert a.filename == "img.png"

    def test_normalize_document_attachment(self) -> None:
        """Non-image/video/audio → type='file', correct filename."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="application/pdf",
            url="https://cdn.discord.com/doc.pdf",
            filename="doc.pdf",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "file"
        assert a.filename == "doc.pdf"
        assert a.mime_type == "application/pdf"

    def test_normalize_multiple_attachments(self) -> None:
        """Multiple non-audio attachments → all in list."""
        adapter = self._make_adapter()
        atts = [
            SimpleNamespace(
                content_type="image/jpeg",
                url="https://cdn/a.jpg",
                filename="a.jpg",
            ),
            SimpleNamespace(
                content_type="application/pdf",
                url="https://cdn/b.pdf",
                filename="b.pdf",
            ),
        ]
        msg = adapter.normalize(
            self._make_msg(attachments=atts),
        )
        assert len(msg.attachments) == 2

    def test_normalize_video_attachment(self) -> None:
        """Video content_type → type='video'."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="video/mp4",
            url="https://cdn.discord.com/clip.mp4",
            filename="clip.mp4",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "video"
        assert a.mime_type == "video/mp4"

    def test_normalize_audio_attachment_excluded(self) -> None:
        """Audio content_type → NOT in attachments list."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="audio/ogg",
            url="https://cdn.discord.com/voice.ogg",
            filename="voice.ogg",
        )
        msg = adapter.normalize(
            self._make_msg(attachments=[att]),
        )
        assert len(msg.attachments) == 0

    def test_normalize_text_only_empty_attachments(self) -> None:
        """Message without attachments → empty list."""
        adapter = self._make_adapter()
        msg = adapter.normalize(self._make_msg())
        assert msg.attachments == []

    def test_normalize_text_and_image(self) -> None:
        """Text AND image → both populated."""
        adapter = self._make_adapter()
        att = SimpleNamespace(
            content_type="image/png",
            url="https://cdn/img.png",
            filename="img.png",
        )
        msg = adapter.normalize(
            self._make_msg(
                content="check this out",
                attachments=[att],
            ),
        )
        assert msg.text == "check this out"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "image"
