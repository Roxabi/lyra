"""Regression tests for _shared_audio.py — SC10 (issue #1540).

Guards the blobstore-driven-port migration: buffer_audio_chunks must delegate
to blob_store.put() and preserve ALL fields the port returns (created_at,
provenance) — no hand-built ContractBlobRef that would silently drop them.

Negative-test design: if blob_store.put() is bypassed and BlobRef is
constructed inline (the pre-migration bug), the created_at assertion fails
because the inline constructor uses `default_factory=lambda: datetime.now(UTC)`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock

import pytest

from factory.adapters.shared._shared_audio import (
    buffer_and_render_audio,
    buffer_audio_chunks,
)
from factory.core.messaging.message import OutboundAudio, OutboundAudioChunk
from factory.core.ports.blobstore import BlobStorePort
from roxabi_contracts import BlobRef

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PINNED_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)

_FAKE_BLOB_REF = BlobRef(
    store_key="sha256:abcdef1234567890",
    content_hash="abcdef1234567890",
    mime="audio/ogg",
    size=8,
    source="lyra-tts",
    filename="voice.ogg",
    platform_ref="tg-file-id-42",
    platform_message_id="msg-99",
    created_at=_PINNED_CREATED_AT,
)


def _make_fake_blob_store(put_return: BlobRef = _FAKE_BLOB_REF) -> BlobStorePort:
    """Build a minimal BlobStorePort fake whose put() returns put_return."""
    fake = AsyncMock()
    fake.put = AsyncMock(return_value=put_return)
    return cast(BlobStorePort, fake)


async def _single_chunk_stream(
    data: bytes = b"OGG_DATA",
    mime: str = "audio/ogg",
) -> AsyncIterator[OutboundAudioChunk]:
    yield OutboundAudioChunk(
        chunk_bytes=data,
        session_id="s1",
        chunk_index=0,
        is_final=True,
        caption="test caption",
        reply_to_id="77",
        mime_type=mime,
    )


async def _multi_chunk_stream() -> AsyncIterator[OutboundAudioChunk]:
    for i in range(3):
        yield OutboundAudioChunk(
            chunk_bytes=f"part{i}".encode(),
            session_id="s1",
            chunk_index=i,
            is_final=(i == 2),
            caption="final caption" if i == 2 else None,
            reply_to_id="200" if i == 2 else None,
            mime_type="audio/ogg",
        )


async def _empty_stream() -> AsyncIterator[OutboundAudioChunk]:
    return
    yield  # pragma: no cover — makes this an async generator


# ---------------------------------------------------------------------------
# SC10 — core regression: created_at and provenance are preserved
# ---------------------------------------------------------------------------


class TestBufferAudioChunksCreatedAtPreservation:
    """The port's BlobRef fields must survive through to the returned OutboundAudio."""

    @pytest.mark.asyncio
    async def test_created_at_is_the_value_port_returned_not_a_fresh_timestamp(
        self,
    ) -> None:
        # Arrange — fake port returns a BlobRef with a pinned created_at
        blob_store = _make_fake_blob_store()

        # Act
        result = await buffer_audio_chunks(
            _single_chunk_stream(),
            blob_store=blob_store,
        )

        # Assert — the regression: if buffer_audio_chunks hand-builds BlobRef
        # instead of delegating to blob_store.put(), created_at would be
        # datetime.now(UTC) (default_factory) and ≠ _PINNED_CREATED_AT.
        assert result is not None
        assert isinstance(result, OutboundAudio)
        assert result.blob_ref.created_at == _PINNED_CREATED_AT

    @pytest.mark.asyncio
    async def test_provenance_fields_carried_through_unchanged(self) -> None:
        # Arrange
        blob_store = _make_fake_blob_store()

        # Act
        result = await buffer_audio_chunks(
            _single_chunk_stream(),
            blob_store=blob_store,
        )

        # Assert — filename, platform_ref, platform_message_id survive verbatim
        assert result is not None
        assert result.blob_ref.filename == "voice.ogg"
        assert result.blob_ref.platform_ref == "tg-file-id-42"
        assert result.blob_ref.platform_message_id == "msg-99"

    @pytest.mark.asyncio
    async def test_blob_ref_is_the_exact_object_returned_by_put(self) -> None:
        # Arrange — identity check: the helper must not copy/reconstruct the ref
        blob_store = _make_fake_blob_store()

        # Act
        result = await buffer_audio_chunks(
            _single_chunk_stream(),
            blob_store=blob_store,
        )

        # Assert — blob_ref is the same frozen object put() returned
        assert result is not None
        assert result.blob_ref is _FAKE_BLOB_REF


# ---------------------------------------------------------------------------
# Delegation: put() is actually awaited with the right arguments
# ---------------------------------------------------------------------------


class TestBufferAudioChunksDelegatestoPort:
    """buffer_audio_chunks must call blob_store.put() — not bypass it."""

    @pytest.mark.asyncio
    async def test_put_awaited_with_buffered_bytes_and_mime(self) -> None:
        # Arrange
        blob_store = _make_fake_blob_store()

        # Act
        await buffer_audio_chunks(
            _single_chunk_stream(data=b"OGG_DATA", mime="audio/ogg"),
            blob_store=blob_store,
        )

        # Assert — proves delegation (not reconstruction)
        # Negative: remove the blob_store.put() call → put is never awaited
        blob_store.put.assert_awaited_once()  # type: ignore[attr-defined]
        positional, kwargs = blob_store.put.call_args  # type: ignore[attr-defined]
        assert positional == (b"OGG_DATA",)
        assert kwargs["mime"] == "audio/ogg"
        assert kwargs["source"] == "lyra-tts"

    @pytest.mark.asyncio
    async def test_put_receives_concatenated_multi_chunk_bytes(self) -> None:
        # Arrange
        blob_store = _make_fake_blob_store()

        # Act
        await buffer_audio_chunks(
            _multi_chunk_stream(),
            blob_store=blob_store,
        )

        # Assert — all three chunks are concatenated before put()
        positional, _ = blob_store.put.call_args  # type: ignore[attr-defined]
        assert positional == (b"part0part1part2",)


# ---------------------------------------------------------------------------
# Return shape: OutboundAudio (not BlobRef)
# ---------------------------------------------------------------------------


class TestBufferAudioChunksReturnShape:
    """Verify the real return type and its fields."""

    @pytest.mark.asyncio
    async def test_returns_outbound_audio_with_correct_caption(self) -> None:
        blob_store = _make_fake_blob_store()

        result = await buffer_audio_chunks(
            _single_chunk_stream(),
            blob_store=blob_store,
        )

        assert result is not None
        assert isinstance(result, OutboundAudio)
        assert result.caption == "test caption"
        assert result.reply_to_id == "77"
        assert result.mime_type == "audio/ogg"

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_stream(self) -> None:
        blob_store = _make_fake_blob_store()

        result = await buffer_audio_chunks(
            _empty_stream(),
            blob_store=blob_store,
        )

        assert result is None
        blob_store.put.assert_not_awaited()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# buffer_and_render_audio — same created_at guarantee through the wrapper
# ---------------------------------------------------------------------------


class TestBufferAndRenderAudioCreatedAt:
    """The outer wrapper must relay the port's BlobRef faithfully to render_fn."""

    @pytest.mark.asyncio
    async def test_render_fn_receives_outbound_audio_with_pinned_created_at(
        self,
    ) -> None:
        # Arrange
        blob_store = _make_fake_blob_store()
        received: list[OutboundAudio] = []

        async def _capture_render_fn(audio: OutboundAudio, inbound: object) -> None:
            received.append(audio)

        from factory.core.auth.trust import TrustLevel
        from factory.core.messaging.message import InboundMessage, TelegramMeta

        inbound = InboundMessage(
            id="msg-test-1540",
            platform="telegram",
            bot_id="main",
            scope_id="chat:1",
            user_id="tg:user:1",
            user_name="Alice",
            is_mention=False,
            text="hi",
            text_raw="hi",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            trust_level=TrustLevel.TRUSTED,
            platform_meta=TelegramMeta(chat_id=1, message_id=1),
        )

        # Act
        await buffer_and_render_audio(
            _single_chunk_stream(),
            inbound,
            _capture_render_fn,
            blob_store=blob_store,
        )

        # Assert — render_fn received an OutboundAudio whose blob_ref.created_at
        # is the pinned value the port returned, NOT a fresh datetime.now(UTC).
        assert len(received) == 1
        assert received[0].blob_ref.created_at == _PINNED_CREATED_AT
