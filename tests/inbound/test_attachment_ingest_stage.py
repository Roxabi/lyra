"""RED tests for AttachmentIngestStage — central voice attachment ingest (#1537, #1551).

All tests call the stage directly:
    AttachmentIngestStage().run(msg, IngestCtx(store=...))

These tests will FAIL at collection until the GREEN implementation lands in
``factory.inbound.attachment_ingest``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from factory.core.audio_payload import AudioPayload
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import InboundMessage
from factory.inbound.attachment_ingest import (  # noqa: E402 — module does not exist yet (RED)
    AttachmentIngestStage,
    IngestCtx,
    PendingAttachment,
)
from roxabi_contracts import BlobRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _voice_msg(
    *,
    pending_attachment: PendingAttachment | None = None,
    blob_ref: BlobRef | None = None,
) -> InboundMessage:
    """Build a minimal voice InboundMessage with unresolved (None) audio payload."""
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
        assertion checks that blob_ref remains None (no mutation, no ingest).
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

        # Assert — blob_ref stays None (unresolved; no ingest attempted)
        assert result.audio is not None
        assert result.audio.blob_ref is None
        # Negative: fetch must NOT have been awaited (store is None → early return)
        fetch_mock.assert_not_awaited()

    async def test_fetch_fail_keeps_pending(self) -> None:
        """fetch() raises → degraded mode: msg returned unchanged; store.put NOT called.

        Negative: if the ``except Exception: return msg`` guard is deleted, the
        exception propagates to the caller instead of returning the original msg,
        which would break the pipeline degradation contract.

        Also asserts ``pending_attachment is None`` — the stage MUST clear the fetch
        closure on the degraded path so the message is safe to serialise over NATS.
        Negative: if the degraded return is changed back to ``return msg`` (without
        ``dataclasses.replace``), ``result.pending_attachment`` would still be the
        ``PendingAttachment`` instance and this assertion would fail.
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

        # Assert — blob_ref stays None on degraded path (fetch failed; no ingest)
        assert result.audio is not None
        assert result.audio.blob_ref is None
        # store.put must NOT have been called when fetch raised
        store_mock.put.assert_not_awaited()
        # Fetch closure MUST be cleared — NATS-safe even on degraded path
        assert result.pending_attachment is None

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
        assert result.audio.blob_ref is not None
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

    async def test_put_failure_degrades_like_fetch_failure(self) -> None:
        """store.put raises → same degraded path as fetch failure.

        B1 fix: the try/except wraps BOTH fetch() and store.put().  Verify that
        a RuntimeError from store.put() follows the same degraded return path:
        blob_ref stays None, pending_attachment cleared, and no exception
        propagates to the caller.

        Negative (a): if the try/except did NOT cover store.put(), the RuntimeError
        would propagate out of stage.run — the test would fail with RuntimeError
        rather than AssertionError.
        Negative (b): if the degraded return did not use ``dataclasses.replace``,
        ``result.pending_attachment`` would still be the PendingAttachment instance
        and assertion (b) would fail.
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
        store_mock = AsyncMock()
        store_mock.put = AsyncMock(side_effect=RuntimeError("blobstore write failed"))
        ctx = IngestCtx(store=store_mock)
        stage = AttachmentIngestStage()

        # Act — must NOT raise despite store.put raising
        result = await stage.run(msg, ctx)

        # Assert (a) — blob_ref stays None on degraded path (put failed; unresolved)
        assert result.audio is not None
        assert result.audio.blob_ref is None
        # Assert (b) — fetch closure cleared (NATS-safe even on degraded path)
        assert result.pending_attachment is None
        # Assert (c) — fetch WAS awaited (fetch succeeded; put was the failure)
        fetch_mock.assert_awaited_once()
        # Assert (d) — put WAS attempted (failure came from put, not fetch)
        store_mock.put.assert_awaited_once()
