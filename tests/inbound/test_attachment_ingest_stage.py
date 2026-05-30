"""RED tests for AttachmentIngestStage — central voice attachment ingest (#1537, #1551).

All tests call the stage directly:
    AttachmentIngestStage().run(msg, IngestCtx(store=...))

These tests will FAIL at collection until the GREEN implementation lands in
``lyra.inbound.attachment_ingest``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from lyra.core.audio_payload import AudioPayload
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage
from lyra.inbound.attachment_ingest import (  # noqa: E402 — module does not exist yet (RED)
    AttachmentIngestStage,
    IngestCtx,
    PendingAttachment,
)
from roxabi_contracts import PENDING_STORE_KEY, BlobRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PENDING_BLOB_REF = BlobRef(
    store_key=PENDING_STORE_KEY,
    content_hash="",
    mime="audio/ogg",
    size=1024,
    source="telegram",
    platform_ref="tg:file_id:ABC123",
    platform_message_id="42",
)

_PENDING_ATTACHMENT = PendingAttachment(
    fetch=AsyncMock(return_value=b"oggbytes"),
    mime="audio/ogg",
    source="telegram",
    platform_ref="tg:file_id:ABC123",
    platform_message_id="42",
)


def _voice_msg(
    *,
    pending_attachment: PendingAttachment | None = None,
    blob_ref: BlobRef = _PENDING_BLOB_REF,
) -> InboundMessage:
    """Build a minimal voice InboundMessage with PENDING audio payload."""
    return InboundMessage(
        id="msg-1",
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
            blob_ref=blob_ref,
            mime_type="audio/ogg",
            duration_ms=3000,
            file_id="ABC123",
        ),
        pending_attachment=pending_attachment,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAttachmentIngestStage:
    """AttachmentIngestStage.run — contract per #1537 locked API."""

    async def test_store_none_is_noop(self) -> None:
        """store=None → stage returns msg unchanged; fetch closure never awaited.

        Negative: if the guard ``if ctx.store is None: return msg`` is deleted,
        the stage will attempt to call ``ctx.store.put(...)`` on None and raise
        AttributeError — the test would then fail for the wrong reason.  This
        assertion checks the PENDING sentinel is preserved (no mutation).
        """
        # Arrange
        fetch_mock = AsyncMock(return_value=b"oggbytes")
        pending = PendingAttachment(
            fetch=fetch_mock,
            mime="audio/ogg",
            source="telegram",
            platform_ref="tg:file_id:ABC123",
            platform_message_id="42",
        )
        msg = _voice_msg(pending_attachment=pending)
        ctx = IngestCtx(store=None)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — blob_ref is still the PENDING sentinel
        assert result.audio is not None
        assert result.audio.blob_ref.store_key == PENDING_STORE_KEY
        # Negative: fetch must NOT have been awaited (store is None → early return)
        fetch_mock.assert_not_awaited()

    async def test_fetch_fail_keeps_pending(self) -> None:
        """fetch() raises → degraded mode: msg returned unchanged; store.put NOT called.

        Negative: if the ``except Exception: return msg`` guard is deleted, the
        exception propagates to the caller instead of returning the original msg,
        which would break the pipeline degradation contract.
        """
        # Arrange
        fetch_mock = AsyncMock(side_effect=RuntimeError("platform fetch failed"))
        pending = PendingAttachment(
            fetch=fetch_mock,
            mime="audio/ogg",
            source="telegram",
            platform_ref="tg:file_id:ABC123",
            platform_message_id="42",
        )
        msg = _voice_msg(pending_attachment=pending)
        store_mock = AsyncMock()
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — PENDING preserved (degraded mode)
        assert result.audio is not None
        assert result.audio.blob_ref.store_key == PENDING_STORE_KEY
        # store.put must NOT have been called when fetch raised
        store_mock.put.assert_not_awaited()

    async def test_happy_path_stamps_real_blob_ref(self) -> None:
        """fetch returns bytes → store.put called once → result carries real BlobRef.

        Checks:
        - result.audio.blob_ref.store_key is the value returned by store.put (≠ PENDING)
        - store.put awaited exactly once with (data, mime=, source=, filename=,
          platform_ref=, platform_message_id=) from the PendingAttachment descriptor
        - result.pending_attachment is None (cleared after successful ingest)

        Negative: if the ``dataclasses.replace(msg, …)`` call does not clear
        ``pending_attachment``, ``result.pending_attachment is None`` fails — the
        stage left stale data on the message.  If ``blob_ref`` is not replaced, the
        store_key remains PENDING and the downstream STT stage would use the wrong
        path.
        """
        # Arrange
        ogg_bytes = b"oggbytes"
        fetch_mock = AsyncMock(return_value=ogg_bytes)
        pending = PendingAttachment(
            fetch=fetch_mock,
            mime="audio/ogg",
            source="telegram",
            platform_ref="tg:file_id:ABC123",
            platform_message_id="42",
            filename=None,
        )
        msg = _voice_msg(pending_attachment=pending)

        real_blob_ref = BlobRef(
            store_key="blob:abc",
            content_hash="sha256deadbeef",
            mime="audio/ogg",
            size=len(ogg_bytes),
            source="telegram",
        )
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(return_value=real_blob_ref)
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act
        result = await stage.run(msg, ctx)

        # Assert — real blob ref stamped on audio
        assert result.audio is not None
        assert result.audio.blob_ref.store_key == "blob:abc"

        # store.put awaited once with the right args from PendingAttachment
        store_mock.put.assert_awaited_once_with(
            ogg_bytes,
            mime=pending.mime,
            source=pending.source,
            filename=pending.filename,
            platform_ref=pending.platform_ref,
            platform_message_id=pending.platform_message_id,
        )

        # pending_attachment cleared after successful ingest
        assert result.pending_attachment is None
