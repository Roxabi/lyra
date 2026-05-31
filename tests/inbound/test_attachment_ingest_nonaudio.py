"""RED tests for AttachmentIngestStage — non-audio attachment ingest (#1552, S5).

Tests the non-audio branch of the central attachment ingest stage:
  - multi-attachment pending list (index-aligned with .attachments)
  - per-item size guard BEFORE fetch
  - BlobStoreServerError → AttachmentIngestError
  - pending_attachments cleared to [] on return (NATS safety)
  - voice path (singular pending_attachment) unchanged
  - source metadata never branches stage logic (axial guard)

These tests will FAIL at collection until the GREEN implementation lands in
``lyra.inbound.attachment_ingest`` (T4).  Missing symbols:
  - MAX_ATTACHMENT_INGEST_BYTES
  - AttachmentIngestError
  - PendingAttachment.size field
  - AttachmentIngestStage.run non-audio loop
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lyra.core.audio_payload import AudioPayload
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import Attachment, InboundMessage
from lyra.inbound.attachment_ingest import (  # noqa: E402 — module imports below do not exist yet (RED)
    MAX_ATTACHMENT_INGEST_BYTES,
    AttachmentIngestError,
    AttachmentIngestStage,
    IngestCtx,
    PendingAttachment,
)
from roxabi_contracts import PENDING_STORE_KEY, BlobRef, BlobStoreServerError

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

_IMAGE_MIME = "image/jpeg"
_DOC_MIME = "application/pdf"


def _wire_ref(store_key: str = "blob:abc", mime: str = _IMAGE_MIME) -> BlobRef:
    """Minimal real (non-PENDING) BlobRef."""
    return BlobRef(
        store_key=store_key,
        content_hash="sha256deadbeef",
        mime=mime,
        size=512,
        source="telegram",
    )


def _pending_attachment(
    *,
    fetch: AsyncMock,
    mime: str = _IMAGE_MIME,
    source: str = "telegram",
    size: int | None = None,
) -> PendingAttachment:
    return PendingAttachment(
        fetch=fetch,
        mime=mime,
        source=source,
        platform_ref="tg:file_id:IMG001",
        platform_message_id="99",
        size=size,
    )


def _attachment(mime: str = _IMAGE_MIME) -> Attachment:
    """Non-audio Attachment with blob_ref=None (pre-ingest)."""
    return Attachment(
        type="image",
        url_or_path_or_bytes="tg:file_id:IMG001",
        mime_type=mime,
    )


def _nonaudio_msg(
    *,
    attachments: list[Attachment],
    pending_attachments: list[PendingAttachment],
    source: str = "telegram",
) -> InboundMessage:
    """Build a minimal non-audio InboundMessage with pending_attachments."""
    return InboundMessage(
        id="msg-na-1",
        platform=source,
        bot_id="bot-1",
        scope_id="chat:100",
        user_id="user:10",
        user_name="tester",
        is_mention=False,
        text="see attached",
        text_raw="see attached",
        trust_level=TrustLevel.PUBLIC,
        attachments=attachments,
        pending_attachments=pending_attachments,
    )


_PENDING_VOICE_BLOB_REF = BlobRef(
    store_key=PENDING_STORE_KEY,
    content_hash="",
    mime="audio/ogg",
    size=1024,
    source="telegram",
    platform_ref="tg:file_id:VOICE42",
    platform_message_id="42",
)


def _voice_msg(*, pending_attachment: PendingAttachment) -> InboundMessage:
    """Minimal voice InboundMessage (mirrors reference fixture)."""
    return InboundMessage(
        id="msg-v-1",
        platform="telegram",
        bot_id="bot-1",
        scope_id="chat:123",
        user_id="user:42",
        user_name="testuser",
        is_mention=False,
        text="",
        text_raw="",
        trust_level=TrustLevel.PUBLIC,
        modality="voice",
        audio=AudioPayload(
            blob_ref=_PENDING_VOICE_BLOB_REF,
            mime_type="audio/ogg",
            duration_ms=3000,
            file_id="VOICE42",
        ),
        pending_attachment=pending_attachment,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAttachmentIngestStageNonAudio:
    """AttachmentIngestStage.run — non-audio multi-attachment contract (#1552)."""

    async def test_single_pending_stamps_blob_ref(self) -> None:
        """Single non-audio pending → attachments[0].blob_ref stamped; pendings cleared.

        Negative: if the non-audio loop is absent (P2 stub), attachments[0].blob_ref
        stays None and the assertion on store_key fails.
        Negative: if pending_attachments is not cleared, the [] assertion fails.
        """
        # Arrange
        image_bytes = b"jpegbytes"
        fetch_mock = AsyncMock(return_value=image_bytes)
        real_ref = _wire_ref("blob:img-001")
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(return_value=real_ref)

        pending = _pending_attachment(fetch=fetch_mock)
        att = _attachment()
        msg = _nonaudio_msg(attachments=[att], pending_attachments=[pending])
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — blob_ref stamped with the wire ref returned by store.put
        assert result.attachments[0].blob_ref is not None
        assert result.attachments[0].blob_ref.store_key == "blob:img-001"
        # pending_attachments cleared (NATS safety)
        assert result.pending_attachments == []

    async def test_multiple_pending_all_stamped(self) -> None:
        """N=3 pending attachments → each attachments[i].blob_ref stamped independently.

        Negative: if the loop stamps only index 0, attachments[1] and [2]
        will still have blob_ref=None and the assertions fail.
        """
        # Arrange
        N = 3
        refs = [_wire_ref(f"blob:img-{i:03d}") for i in range(N)]
        fetches = [AsyncMock(return_value=b"bytes") for _ in range(N)]
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(side_effect=refs)

        pendings = [_pending_attachment(fetch=fetches[i]) for i in range(N)]
        atts = [_attachment() for _ in range(N)]
        msg = _nonaudio_msg(attachments=atts, pending_attachments=pendings)
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — all three blob_refs stamped with distinct wire refs
        for i, expected_key in enumerate(f"blob:img-{j:03d}" for j in range(N)):
            blob_ref_i = result.attachments[i].blob_ref
            assert blob_ref_i is not None, f"index {i} not stamped"
            assert blob_ref_i.store_key == expected_key, f"index {i} wrong ref"
        assert result.pending_attachments == []

    async def test_oversize_raises_before_fetch(self) -> None:
        """declared size > MAX_ATTACHMENT_INGEST_BYTES → AttachmentIngestError;
        fetch never awaited.

        This is the critical negative: if the size guard is deleted, fetch() will
        be called (downloading a huge file) and no AttachmentIngestError is raised.
        The test verifies BOTH the exception AND the zero-fetch invariant.
        """
        # Arrange
        fetch_mock = AsyncMock(side_effect=AssertionError("fetch must not be called"))
        oversize = MAX_ATTACHMENT_INGEST_BYTES + 1
        pending = _pending_attachment(fetch=fetch_mock, size=oversize)
        att = _attachment()
        msg = _nonaudio_msg(attachments=[att], pending_attachments=[pending])
        store_mock = AsyncMock()
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act + Assert — must raise before awaiting fetch
        with pytest.raises(AttachmentIngestError):
            await stage.run(msg, ctx)

        # Negative guard: if fetch was called, this fails
        fetch_mock.assert_not_awaited()

    async def test_store_put_server_error_raises_attachment_ingest_error(self) -> None:
        """store.put raises BlobStoreServerError → AttachmentIngestError raised.

        Negative: if the except clause is absent, BlobStoreServerError propagates
        directly to the caller instead of being wrapped in AttachmentIngestError.
        """
        # Arrange
        fetch_mock = AsyncMock(return_value=b"jpegbytes")
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(
            side_effect=BlobStoreServerError("upstream 503", status_code=503)
        )
        pending = _pending_attachment(fetch=fetch_mock)
        att = _attachment()
        msg = _nonaudio_msg(attachments=[att], pending_attachments=[pending])
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act + Assert
        with pytest.raises(AttachmentIngestError):
            await stage.run(msg, ctx)

    async def test_store_none_is_passthrough_nonaudio(self) -> None:
        """store=None → no-op passthrough; attachments unchanged; no blob_ref set.

        Negative: if the ``if ctx.store is None: return msg`` guard is removed,
        the stage attempts store.put(None, ...) and raises AttributeError instead
        of silently returning the original message.
        """
        # Arrange
        fetch_mock = AsyncMock(return_value=b"jpegbytes")
        pending = _pending_attachment(fetch=fetch_mock)
        att = _attachment()
        msg = _nonaudio_msg(attachments=[att], pending_attachments=[pending])
        ctx = IngestCtx(store=None)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — attachments unchanged (blob_ref still None)
        assert result.attachments[0].blob_ref is None
        # fetch must NOT be awaited (store is None → early return)
        fetch_mock.assert_not_awaited()

    async def test_voice_path_unchanged(self) -> None:
        """Voice path (msg.audio set + singular pending_attachment) → blob_ref stamped.

        This test mirrors the reference test_attachment_ingest_stage.py happy path.
        Non-audio changes must not regress the existing voice contract.
        """
        # Arrange
        ogg_bytes = b"oggbytes"
        fetch_mock = AsyncMock(return_value=ogg_bytes)
        voice_pending = PendingAttachment(
            fetch=fetch_mock,
            mime="audio/ogg",
            source="telegram",
            platform_ref="tg:file_id:VOICE42",
            platform_message_id="42",
            filename=None,
        )
        real_audio_ref = BlobRef(
            store_key="blob:audio-xyz",
            content_hash="sha256audio",
            mime="audio/ogg",
            size=len(ogg_bytes),
            source="telegram",
        )
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(return_value=real_audio_ref)
        ctx = IngestCtx(store=store_mock)
        msg = _voice_msg(pending_attachment=voice_pending)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — audio.blob_ref updated to the real wire ref
        assert result.audio is not None
        assert result.audio.blob_ref.store_key == "blob:audio-xyz"
        # pending_attachment cleared (singular voice path)
        assert result.pending_attachment is None

    @pytest.mark.parametrize("source", ["telegram", "discord"])
    async def test_source_does_not_branch(self, source: str) -> None:
        """source="telegram" and source="discord" produce identical stamping logic.

        Axial guard: the stage must NOT inspect PendingAttachment.source to
        branch behaviour. Both sources must result in blob_ref being stamped.

        Negative: if the stage had ``if p.source == "telegram": ... else: skip``,
        the discord case would leave blob_ref=None and the assertion would fail.
        """
        # Arrange
        image_bytes = b"imgbytes"
        fetch_mock = AsyncMock(return_value=image_bytes)
        real_ref = _wire_ref(f"blob:{source}-001")
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(return_value=real_ref)

        pending = _pending_attachment(fetch=fetch_mock, source=source)
        att = _attachment()
        msg = _nonaudio_msg(
            attachments=[att], pending_attachments=[pending], source=source
        )
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — blob_ref stamped regardless of source
        assert result.attachments[0].blob_ref is not None
        assert result.attachments[0].blob_ref.store_key == f"blob:{source}-001"
        assert result.pending_attachments == []
