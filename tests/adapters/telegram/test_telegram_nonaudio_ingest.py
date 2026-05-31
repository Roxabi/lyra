"""RED tests — Telegram non-audio attachment ingest (T7, #1552).

Covers:
1. photo update → normalize() sets pending_attachments (PendingAttachment per
   Attachment, index-aligned), with fetch closure, declared size, source="telegram",
   platform_ref=file_id.
2. document update → same; filename carried on PendingAttachment.
3. Through pipeline with fake store → attachments[0].blob_ref is a real BlobRef
   before hub enqueue.
4. Oversize (declared file_size > MAX_ATTACHMENT_INGEST_BYTES) → AttachmentIngestError
   raised; adapter sends "too large" reply via adapter.bot.send_message; no hub push.

These tests are RED until T8 (telegram closures) + T9 (telegram boundary reply) land.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.adapters.telegram import TelegramAdapter
from lyra.adapters.telegram.telegram_normalize import normalize
from lyra.inbound.attachment_ingest import PendingAttachment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> TelegramAdapter:
    """Minimal TelegramAdapter with mocked bot — no real HTTP."""
    import io

    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=MagicMock(),
    )
    bot_mock = AsyncMock()
    # get_file returns a File-like object with .file_path
    file_stub = SimpleNamespace(file_id="file_id_stub", file_path="path/to/file")
    bot_mock.get_file = AsyncMock(return_value=file_stub)
    # download_file returns a BytesIO (matches aiogram's real return type)
    bot_mock.download_file = AsyncMock(return_value=io.BytesIO(b"<fake-bytes>"))
    bot_mock.send_message = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def _make_base_msg() -> SimpleNamespace:
    """Base Telegram message skeleton (no media)."""
    from datetime import datetime, timezone

    return SimpleNamespace(
        chat=SimpleNamespace(id=100, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        message_thread_id=None,
        message_id=9,
        date=datetime.now(timezone.utc),
        voice=None,
        audio=None,
        video_note=None,
        reply_to_message=None,
        text="",
        entities=None,
        caption=None,
        photo=None,
        document=None,
        video=None,
        animation=None,
        sticker=None,
    )


def _make_photo_msg(
    file_id: str = "photo_001", file_size: int = 1024
) -> SimpleNamespace:
    """Telegram message carrying a photo (list of PhotoSize, largest last)."""
    base = _make_base_msg()
    photo_size = SimpleNamespace(
        file_id=file_id, file_size=file_size, width=800, height=600
    )
    base.photo = [photo_size]
    return base


def _make_document_msg(
    file_id: str = "doc_001",
    file_size: int = 2048,
    file_name: str = "report.pdf",
    mime_type: str = "application/pdf",
) -> SimpleNamespace:
    """Telegram message carrying a document."""
    base = _make_base_msg()
    base.document = SimpleNamespace(
        file_id=file_id,
        file_size=file_size,
        file_name=file_name,
        mime_type=mime_type,
    )
    return base


# ---------------------------------------------------------------------------
# Test 1: photo → pending_attachments set with correct PendingAttachment shape
# ---------------------------------------------------------------------------


def test_tg_photo_normalize_sets_pending_attachments() -> None:
    """normalize() on a photo message sets pending_attachments index-aligned.

    Each PendingAttachment must have:
    - fetch: async callable
    - mime: "image/jpeg" (photo default)
    - source: "telegram"
    - size: photo.file_size
    - platform_ref: photo.file_id
    """
    adapter = _make_adapter()
    raw = _make_photo_msg(file_id="photo_abc", file_size=5000)

    msg = normalize(adapter, raw)

    assert len(msg.attachments) == 1
    assert len(msg.pending_attachments) == 1

    pa: PendingAttachment = msg.pending_attachments[0]
    assert isinstance(pa, PendingAttachment)
    assert callable(pa.fetch)
    assert pa.mime == "image/jpeg"
    assert pa.source == "telegram"
    assert pa.size == 5000
    assert pa.platform_ref == "photo_abc"


# ---------------------------------------------------------------------------
# Test 2: document → pending_attachments carries filename
# ---------------------------------------------------------------------------


def test_tg_document_normalize_carries_filename() -> None:
    """normalize() on a document message carries filename in PendingAttachment."""
    adapter = _make_adapter()
    raw = _make_document_msg(
        file_id="doc_xyz",
        file_size=3000,
        file_name="report.pdf",
        mime_type="application/pdf",
    )

    msg = normalize(adapter, raw)

    assert len(msg.attachments) == 1
    assert len(msg.pending_attachments) == 1

    pa: PendingAttachment = msg.pending_attachments[0]
    assert pa.filename == "report.pdf"
    assert pa.mime == "application/pdf"
    assert pa.source == "telegram"
    assert pa.size == 3000
    assert pa.platform_ref == "doc_xyz"


# ---------------------------------------------------------------------------
# Test 3: through pipeline + fake store → blob_ref stamped on attachments[0]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_photo_pipeline_stamps_blob_ref() -> None:
    """Photo message through the full inbound pipeline stamps attachments[0].blob_ref.

    The fake store.put() returns a real BlobRef. After pipeline.run(), the
    InboundMessage dispatched to the hub has attachments[0].blob_ref set.

    Negative-test contract: if the stage's non-audio loop is deleted (P2 dead branch),
    blob_ref remains None and the assertion fails.
    """
    from lyra.adapters.telegram.telegram_inbound import handle_message
    from lyra.inbound.attachment_ingest import IngestCtx
    from roxabi_contracts import BlobRef

    adapter = _make_adapter()
    raw = _make_photo_msg(file_id="photo_pipeline", file_size=1024)

    fake_blob_ref = BlobRef(
        store_key="s3://bucket/photo_pipeline",
        content_hash="abc123",
        mime="image/jpeg",
        size=1024,
        source="telegram",
        platform_ref="photo_pipeline",
    )

    fake_store = AsyncMock()
    fake_store.put = AsyncMock(return_value=fake_blob_ref)
    fake_ingest_ctx = IngestCtx(store=fake_store)

    # Inject ingest_ctx so the stage runs
    adapter._ingest_ctx = fake_ingest_ctx  # type: ignore[attr-defined]

    # Capture the dispatched InboundMessage
    captured: list[Any] = []

    async def _capture_dispatch(msg: Any, ctx: Any, send_bp: Any, on_drop: Any) -> None:
        captured.append(msg)

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = _capture_dispatch

    with patch("lyra.adapters.telegram.telegram_inbound._pipeline") as mock_pipeline:
        # Build a real InboundPipeline but intercept dispatcher
        from lyra.inbound.attachment_ingest import AttachmentIngestStage
        from lyra.inbound.pipeline import InboundPipeline
        from lyra.inbound.router import Router
        from lyra.inbound.session_builder import SessionBuilder

        real_pipeline = InboundPipeline(
            router=Router(),
            session_builder=SessionBuilder(),
            dispatcher=mock_dispatcher,  # type: ignore[arg-type]
            ingest_stage=AttachmentIngestStage(),
        )
        mock_pipeline.run = real_pipeline.run

        await handle_message(adapter, raw)

    assert len(captured) == 1, "Dispatcher must have been called exactly once"
    dispatched_msg = captured[0]
    assert len(dispatched_msg.attachments) >= 1
    assert dispatched_msg.attachments[0].blob_ref == fake_blob_ref


# ---------------------------------------------------------------------------
# Test 4: oversize → AttachmentIngestError → adapter replies; no hub push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_oversize_photo_reply_no_hub_push() -> None:
    """Oversize declared file_size → AttachmentIngestError is caught at the boundary.

    The adapter must:
    (a) send_message with a "too large" user-facing message
    (b) NOT push to the hub (no dispatcher call)

    Negative-test contract: if the AttachmentIngestError catch in handle_message
    is removed, the error propagates uncaught and no send_message is called —
    the assertion on send_message fails.
    """
    from lyra.adapters.telegram.telegram_inbound import handle_message
    from lyra.inbound.attachment_ingest import MAX_ATTACHMENT_INGEST_BYTES, IngestCtx

    adapter = _make_adapter()
    # file_size declared as one byte over the cap
    raw = _make_photo_msg(
        file_id="photo_big", file_size=MAX_ATTACHMENT_INGEST_BYTES + 1
    )

    fake_store = AsyncMock()
    fake_store.put = AsyncMock()  # should never be called
    fake_ingest_ctx = IngestCtx(store=fake_store)
    adapter._ingest_ctx = fake_ingest_ctx  # type: ignore[attr-defined]

    captured_dispatches: list[Any] = []

    async def _capture_dispatch(msg: Any, ctx: Any, send_bp: Any, on_drop: Any) -> None:
        captured_dispatches.append(msg)

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = _capture_dispatch

    with patch("lyra.adapters.telegram.telegram_inbound._pipeline") as mock_pipeline:
        from lyra.inbound.attachment_ingest import AttachmentIngestStage
        from lyra.inbound.pipeline import InboundPipeline
        from lyra.inbound.router import Router
        from lyra.inbound.session_builder import SessionBuilder

        real_pipeline = InboundPipeline(
            router=Router(),
            session_builder=SessionBuilder(),
            dispatcher=mock_dispatcher,  # type: ignore[arg-type]
            ingest_stage=AttachmentIngestStage(),
        )
        mock_pipeline.run = real_pipeline.run

        await handle_message(adapter, raw)

    # (a) Adapter sent a "too large" message to the user
    adapter.bot.send_message.assert_called_once()
    call_kwargs = adapter.bot.send_message.call_args
    # send_message is called with keyword arguments via _make_send_kwargs (text=...)
    text_sent: str = call_kwargs.kwargs.get("text", "") or str(call_kwargs)
    assert "large" in text_sent.lower(), (
        f"Expected 'large' in reply, got: {text_sent!r}"
    )

    # (b) No hub push — dispatcher was never called
    assert len(captured_dispatches) == 0, (
        "Hub must NOT be reached on oversize attachment"
    )


# ---------------------------------------------------------------------------
# Test 5: sticker exclusion — static included, animated + video excluded
# ---------------------------------------------------------------------------


def _make_sticker_msg(
    file_id: str = "sticker_001",
    file_size: int = 512,
    *,
    is_animated: bool = False,
    is_video: bool = False,
) -> SimpleNamespace:
    """Telegram message carrying a sticker."""
    base = _make_base_msg()
    base.sticker = SimpleNamespace(
        file_id=file_id,
        file_size=file_size,
        is_animated=is_animated,
        is_video=is_video,
    )
    return base


def test_tg_static_sticker_included() -> None:
    """Static WebP sticker (is_animated=False, is_video=False) → included."""
    adapter = _make_adapter()
    raw = _make_sticker_msg(file_id="sticker_static", is_animated=False, is_video=False)

    msg = normalize(adapter, raw)

    assert len(msg.attachments) == 1
    assert len(msg.pending_attachments) == 1
    pa: PendingAttachment = msg.pending_attachments[0]
    assert pa.mime == "image/webp"
    assert pa.source == "telegram"
    assert pa.platform_ref == "sticker_static"


def test_tg_animated_sticker_excluded() -> None:
    """Animated sticker (is_animated=True) → excluded (empty attachment lists)."""
    adapter = _make_adapter()
    raw = _make_sticker_msg(is_animated=True, is_video=False)

    msg = normalize(adapter, raw)

    assert len(msg.attachments) == 0
    assert len(msg.pending_attachments) == 0


def test_tg_video_sticker_excluded() -> None:
    """Video sticker (is_video=True) → excluded (empty attachment lists)."""
    adapter = _make_adapter()
    raw = _make_sticker_msg(is_animated=False, is_video=True)

    msg = normalize(adapter, raw)

    assert len(msg.attachments) == 0
    assert len(msg.pending_attachments) == 0
