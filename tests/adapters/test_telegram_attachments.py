"""Tests for TelegramAdapter inbound attachment extraction (#183).

Covers: TestTelegramAttachments — photo, document, video, animation, sticker
normalization, caption handling, and empty attachment list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest  # noqa: F401 — collected by pytest

from lyra.core.trust import TrustLevel  # noqa: F401


class TestTelegramAttachments:
    """TelegramAdapter.normalize() extracts non-audio attachments."""

    def _make_adapter(self):
        from lyra.adapters.telegram import TelegramAdapter

        hub = MagicMock()
        return TelegramAdapter(bot_id="main", token="test-token-secret", hub=hub)

    def _make_msg(  # noqa: PLR0913 — test factory with optional overrides
        self,
        *,
        text: str | None = "hello",
        caption=None,
        photo=None,
        document=None,
        video=None,
        animation=None,
        sticker=None,
    ):
        return SimpleNamespace(
            chat=SimpleNamespace(id=123, type="private"),
            from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
            text=text,
            caption=caption,
            date=datetime.now(timezone.utc),
            message_thread_id=None,
            message_id=99,
            entities=None,
            photo=photo,
            document=document,
            video=video,
            animation=animation,
            sticker=sticker,
        )

    def test_normalize_photo_attachment(self) -> None:
        """Photo → type='image', tg:file_id: prefix, image/jpeg."""
        adapter = self._make_adapter()
        photo = [
            SimpleNamespace(file_id="small123"),
            SimpleNamespace(file_id="large456"),
        ]
        msg = adapter.normalize(self._make_msg(photo=photo))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.url_or_path_or_bytes == "tg:file_id:large456"
        assert a.mime_type == "image/jpeg"

    def test_normalize_photo_with_caption(self) -> None:
        """Photo with caption → caption in text, photo in attachments."""
        adapter = self._make_adapter()
        photo = [SimpleNamespace(file_id="pic123")]
        msg = adapter.normalize(
            self._make_msg(
                text=None,
                caption="Look at this!",
                photo=photo,
            ),
        )
        assert msg.text == "Look at this!"
        assert len(msg.attachments) == 1
        assert msg.attachments[0].type == "image"

    def test_normalize_document_attachment(self) -> None:
        """Document → type='file', correct mime_type and filename."""
        adapter = self._make_adapter()
        doc = SimpleNamespace(
            file_id="doc789",
            mime_type="application/pdf",
            file_name="report.pdf",
        )
        msg = adapter.normalize(self._make_msg(document=doc))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "file"
        assert a.url_or_path_or_bytes == "tg:file_id:doc789"
        assert a.mime_type == "application/pdf"
        assert a.filename == "report.pdf"

    def test_normalize_video_attachment(self) -> None:
        """Video → type='video'."""
        adapter = self._make_adapter()
        vid = SimpleNamespace(file_id="vid101", mime_type="video/mp4")
        msg = adapter.normalize(self._make_msg(video=vid))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "video"
        assert a.url_or_path_or_bytes == "tg:file_id:vid101"

    def test_normalize_animation_attachment(self) -> None:
        """Animation (GIF) → type='image', image/gif."""
        adapter = self._make_adapter()
        anim = SimpleNamespace(file_id="gif999")
        msg = adapter.normalize(
            self._make_msg(animation=anim),
        )
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.mime_type == "image/gif"
        assert a.url_or_path_or_bytes == "tg:file_id:gif999"

    def test_normalize_animated_sticker_skipped(self) -> None:
        """Animated sticker (is_animated=True) → NOT in attachments."""
        adapter = self._make_adapter()
        sticker = SimpleNamespace(file_id="stk1", is_animated=True, is_video=False)
        msg = adapter.normalize(self._make_msg(sticker=sticker))
        assert len(msg.attachments) == 0

    def test_normalize_video_sticker_skipped(self) -> None:
        """Video sticker (is_video=True) → NOT in attachments."""
        adapter = self._make_adapter()
        sticker = SimpleNamespace(
            file_id="stk3",
            is_animated=False,
            is_video=True,
        )
        msg = adapter.normalize(
            self._make_msg(sticker=sticker),
        )
        assert len(msg.attachments) == 0

    def test_normalize_static_sticker(self) -> None:
        """Static sticker → type='image', image/webp."""
        adapter = self._make_adapter()
        sticker = SimpleNamespace(file_id="stk2", is_animated=False, is_video=False)
        msg = adapter.normalize(self._make_msg(sticker=sticker))
        assert len(msg.attachments) == 1
        a = msg.attachments[0]
        assert a.type == "image"
        assert a.mime_type == "image/webp"
        assert a.url_or_path_or_bytes == "tg:file_id:stk2"

    def test_normalize_text_only_empty_attachments(self) -> None:
        """Text-only message → empty attachments list."""
        adapter = self._make_adapter()
        msg = adapter.normalize(self._make_msg())
        assert msg.attachments == []
