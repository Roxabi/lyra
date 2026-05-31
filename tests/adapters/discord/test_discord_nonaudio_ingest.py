"""RED tests — Discord non-audio attachment ingest (T11, #1552).

Covers:
1. Message with multiple attachments → discord_normalize.normalize() sets
   pending_attachments index-aligned with attachments; each PendingAttachment has
   fetch (wraps a.read), size==a.size, source=="discord", mime==content_type.
2. Through pipeline with fake store → EACH attachments[i].blob_ref is stamped.
3. Oversize (attachment.size > cap) → AttachmentIngestError → in-channel reply
   via adapter boundary; no hub push.

These tests are RED until T12 (discord closures) + T13 (discord boundary reply) land.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.discord.discord_normalize import normalize
from lyra.inbound.attachment_ingest import PendingAttachment

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> DiscordAdapter:
    """Minimal DiscordAdapter — no real gateway connection."""
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    return adapter


def _make_discord_message(
    attachments: list[Any] | None = None,
) -> SimpleNamespace:
    """Discord-like message in a DM (guild=None)."""
    import datetime

    return SimpleNamespace(
        guild=None,
        channel=SimpleNamespace(id=555),
        author=SimpleNamespace(
            id=77,
            name="Bob",
            display_name="Bob",
            bot=False,
            roles=[],
        ),
        id=888,
        content="",
        mentions=[],
        created_at=datetime.datetime.now(datetime.timezone.utc),
        reply=AsyncMock(),
        reference=None,
        attachments=attachments or [],
    )


def _make_discord_attachment(
    url: str = "https://cdn.discord.com/file.png",
    size: int = 1024,
    content_type: str = "image/png",
    filename: str = "file.png",
) -> SimpleNamespace:
    """discord.Attachment-like stub with async .read()."""
    data = b"\x89PNG" + b"\x00" * 20  # PNG magic
    return SimpleNamespace(
        url=url,
        size=size,
        content_type=content_type,
        filename=filename,
        read=AsyncMock(return_value=data),
    )


# ---------------------------------------------------------------------------
# Test 1: multiple attachments → pending_attachments index-aligned
# ---------------------------------------------------------------------------


def test_dc_multi_attachment_normalize_sets_pending_attachments() -> None:
    """normalize() with 3 attachments sets pending_attachments index-aligned.

    Each PendingAttachment must have:
    - fetch: wraps a.read (async callable)
    - size: == a.size
    - source: "discord"
    - mime: == a.content_type
    """
    adapter = _make_adapter()

    att1 = _make_discord_attachment(
        url="https://cdn/img1.png",
        size=1000,
        content_type="image/png",
        filename="img1.png",
    )
    att2 = _make_discord_attachment(
        url="https://cdn/doc.pdf",
        size=2000,
        content_type="application/pdf",
        filename="doc.pdf",
    )
    att3 = _make_discord_attachment(
        url="https://cdn/vid.mp4",
        size=3000,
        content_type="video/mp4",
        filename="vid.mp4",
    )
    raw = _make_discord_message(attachments=[att1, att2, att3])

    msg = normalize(adapter, raw)

    assert len(msg.attachments) == 3
    assert len(msg.pending_attachments) == 3

    for i, (_att, pa) in enumerate(zip(msg.attachments, msg.pending_attachments)):
        assert isinstance(pa, PendingAttachment), (
            f"pending_attachments[{i}] must be PendingAttachment"
        )
        assert callable(pa.fetch), f"pending_attachments[{i}].fetch must be callable"
        assert pa.source == "discord", (
            f"pending_attachments[{i}].source must be 'discord'"
        )

    # Index 0 — image
    assert msg.pending_attachments[0].size == 1000
    assert msg.pending_attachments[0].mime == "image/png"

    # Index 1 — document
    assert msg.pending_attachments[1].size == 2000
    assert msg.pending_attachments[1].mime == "application/pdf"

    # Index 2 — video
    assert msg.pending_attachments[2].size == 3000
    assert msg.pending_attachments[2].mime == "video/mp4"


# ---------------------------------------------------------------------------
# Test 2: through pipeline + fake store → each blob_ref stamped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_multi_attachment_pipeline_stamps_all_blob_refs() -> None:
    """3 attachments through the full inbound pipeline → each blob_ref stamped.

    The fake store.put() returns a distinct BlobRef per call.
    After pipeline.run(), every dispatched msg.attachments[i].blob_ref is real.

    Negative-test contract: if the non-audio loop in AttachmentIngestStage is
    deleted (P2 dead branch), all blob_refs remain None — assertion fails.
    """
    from lyra.adapters.discord.discord_inbound import handle_message
    from lyra.inbound.attachment_ingest import IngestCtx
    from roxabi_contracts import BlobRef

    adapter = _make_adapter()

    att1 = _make_discord_attachment(
        url="https://cdn/a.png", size=500, content_type="image/png"
    )
    att2 = _make_discord_attachment(
        url="https://cdn/b.pdf", size=600, content_type="application/pdf"
    )
    att3 = _make_discord_attachment(
        url="https://cdn/c.mp4", size=700, content_type="video/mp4"
    )
    raw = _make_discord_message(attachments=[att1, att2, att3])

    # Distinct BlobRefs per put() call
    fake_refs = [
        BlobRef(
            store_key=f"s3://bucket/att{i}",
            content_hash=f"hash{i}",
            mime="image/png",
            size=100,
            source="discord",
        )
        for i in range(3)
    ]
    call_count = [0]

    async def _fake_put(*args: Any, **kwargs: Any) -> BlobRef:
        ref = fake_refs[call_count[0]]
        call_count[0] += 1
        return ref

    fake_store = AsyncMock()
    fake_store.put = _fake_put
    fake_ingest_ctx = IngestCtx(store=fake_store)
    adapter._ingest_ctx = fake_ingest_ctx  # type: ignore[attr-defined]

    captured: list[Any] = []

    async def _capture_dispatch(msg: Any, ctx: Any, send_bp: Any, on_drop: Any) -> None:
        captured.append(msg)

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = _capture_dispatch

    with patch("lyra.adapters.discord.discord_inbound._pipeline") as mock_pipeline:
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

    assert len(captured) == 1, "Dispatcher must be called exactly once"
    dispatched_msg = captured[0]
    assert len(dispatched_msg.attachments) == 3

    for i in range(3):
        assert dispatched_msg.attachments[i].blob_ref is not None, (
            f"attachments[{i}].blob_ref must be set after pipeline"
        )
        assert dispatched_msg.attachments[i].blob_ref == fake_refs[i]


# ---------------------------------------------------------------------------
# Test 3: oversize → AttachmentIngestError → in-channel reply; no hub push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_oversize_attachment_reply_no_hub_push() -> None:
    """attachment.size > cap → AttachmentIngestError caught at boundary.

    The adapter must:
    (a) await message.reply() with the "too large" user-facing text
    (b) NOT push to the hub (no dispatcher call)

    Negative-test contract: if the AttachmentIngestError catch in handle_message
    is removed, the error propagates uncaught and message.reply is never called —
    assertion fails.
    """
    from lyra.adapters.discord.discord_inbound import handle_message
    from lyra.inbound.attachment_ingest import MAX_ATTACHMENT_INGEST_BYTES, IngestCtx

    adapter = _make_adapter()

    # One oversized attachment
    big_att = _make_discord_attachment(
        url="https://cdn/big.zip",
        size=MAX_ATTACHMENT_INGEST_BYTES + 1,
        content_type="application/zip",
        filename="big.zip",
    )
    raw = _make_discord_message(attachments=[big_att])

    fake_store = AsyncMock()
    fake_store.put = AsyncMock()  # must never be called
    fake_ingest_ctx = IngestCtx(store=fake_store)
    adapter._ingest_ctx = fake_ingest_ctx  # type: ignore[attr-defined]

    captured_dispatches: list[Any] = []

    async def _capture_dispatch(msg: Any, ctx: Any, send_bp: Any, on_drop: Any) -> None:
        captured_dispatches.append(msg)

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = _capture_dispatch

    with patch("lyra.adapters.discord.discord_inbound._pipeline") as mock_pipeline:
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

    # (a) In-channel reply was sent
    raw.reply.assert_awaited_once()
    reply_text: str = raw.reply.call_args.args[0]
    assert "large" in reply_text.lower(), (
        f"Expected 'large' in reply text, got: {reply_text!r}"
    )

    # (b) No hub push
    assert len(captured_dispatches) == 0, (
        "Hub must NOT be reached on oversize attachment"
    )
