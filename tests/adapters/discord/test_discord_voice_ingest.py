"""GREEN tests — Discord voice ingest routing via InboundPipeline (#1537 / #1551).

Eager-read design (corrected from original deferred-read spec):
- handle_audio() reads attachment bytes synchronously BEFORE routing via _pipeline.
- Bytes are wrapped in a trivial closure (FetchFn) so AttachmentIngestStage can
  call store.put() uniformly; the fetch is a local memory read, not a CDN round-trip.
- audio_attachment.read IS awaited eagerly in the handler (before _pipeline.run).
- The routed InboundMessage carries a non-None pending_attachment.
- _pipeline.run is awaited exactly once.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.discord.discord_audio import handle_audio
from lyra.core.auth.trust import TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OGG_MAGIC = b"OggS" + b"\x00" * 20  # valid magic bytes to pass format gate


def _make_adapter() -> DiscordAdapter:
    """Minimal DiscordAdapter — no real gateway connection."""
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    return adapter


def _make_discord_message() -> SimpleNamespace:
    """Discord-like message in a DM (guild=None so audio gate passes)."""
    return SimpleNamespace(
        guild=None,  # DM — bypasses the DM/mention/owned-thread gate
        channel=SimpleNamespace(id=333),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        id=777,
        mentions=[],
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        reply=AsyncMock(),
    )


def _make_audio_attachment(size: int = 1024) -> SimpleNamespace:
    """Attachment whose read() returns valid OGG bytes."""
    return SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.discord.com/audio.ogg",
        size=size,
        read=AsyncMock(return_value=_OGG_MAGIC),
    )


# ---------------------------------------------------------------------------
# T4-1: handle_audio routes via _pipeline.run (not push_to_hub_guarded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_voice_routes_via_pipeline_not_direct_push() -> None:
    """handle_audio() must await _pipeline.run() exactly once."""
    adapter = _make_adapter()
    message = _make_discord_message()
    audio_attachment = _make_audio_attachment()

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    # handle_audio shares discord_inbound._pipeline (single pipeline per adapter)
    with patch("lyra.adapters.discord.discord_inbound._pipeline", mock_pipeline):
        await handle_audio(adapter, message, audio_attachment, TrustLevel.PUBLIC)

    # Assert: pipeline.run was called exactly once
    mock_pipeline.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# T4-2: audio_attachment.read IS awaited eagerly; routed message carries pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_eager_read_and_pending_attachment_routed() -> None:
    """handle_audio() reads eagerly AND routes a non-None pending_attachment.

    Eager design: read + magic-check fire synchronously in the handler (before
    _pipeline.run) so download errors produce user-facing replies immediately.
    The already-fetched bytes are wrapped in a trivial FetchFn closure and passed
    as pending_attachment on the InboundMessage so AttachmentIngestStage can
    call store.put() without a second CDN round-trip.
    """
    adapter = _make_adapter()
    message = _make_discord_message()
    audio_attachment = _make_audio_attachment()

    # Capture the InboundMessage that _pipeline.run receives.
    captured_msg: list = []

    async def _capture_run(raw, ctx, parser, **kwargs):  # noqa: ARG001
        msg = parser.parse(raw, ctx)
        captured_msg.append(msg)

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(side_effect=_capture_run)

    # handle_audio shares discord_inbound._pipeline (single pipeline per adapter)
    with patch("lyra.adapters.discord.discord_inbound._pipeline", mock_pipeline):
        await handle_audio(adapter, message, audio_attachment, TrustLevel.PUBLIC)

    # Attachment was read eagerly (synchronously in the handler).
    audio_attachment.read.assert_awaited_once()

    # pipeline.run was called once.
    mock_pipeline.run.assert_awaited_once()

    # The routed message carries a non-None pending_attachment (FetchFn closure).
    assert len(captured_msg) == 1
    routed = captured_msg[0]
    assert routed.pending_attachment is not None


# ---------------------------------------------------------------------------
# T4-3: too-large early return — pipeline never awaited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_too_large_reply_no_pipeline() -> None:
    """When attachment size exceeds _max_audio_bytes, handler must:
    (a) await message.reply() with the audio_too_large text, and
    (b) NOT await discord_inbound._pipeline.run.

    Negative-test contract: deleting the early `return` after the size check
    would cause _pipeline.run to be awaited — assert_not_awaited() catches it.
    """
    adapter = _make_adapter()
    message = _make_discord_message()
    # Size exactly one byte over the limit
    oversized = _make_audio_attachment(size=adapter._max_audio_bytes + 1)

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    with patch("lyra.adapters.discord.discord_inbound._pipeline", mock_pipeline):
        await handle_audio(adapter, message, oversized, TrustLevel.PUBLIC)

    # (a) Reply was sent
    message.reply.assert_awaited_once()
    reply_text: str = message.reply.call_args.args[0]
    assert "large" in reply_text.lower()

    # (b) Pipeline was NOT reached — load-bearing assertion
    mock_pipeline.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# T4-4: download failure early return — pipeline never awaited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_download_failed_reply_no_pipeline() -> None:
    """When audio_attachment.read() raises discord.HTTPException, handler must:
    (a) await message.reply() with the audio_download_failed text, and
    (b) NOT await discord_inbound._pipeline.run.

    Negative-test contract: deleting the early `return` after the download-failure
    branch would cause _pipeline.run to be awaited — assert_not_awaited() catches it.
    """
    adapter = _make_adapter()
    message = _make_discord_message()
    attachment = _make_audio_attachment()
    # Override read to raise — HTTPException is in the except tuple
    resp_mock = MagicMock(status=503, headers={})
    attachment.read = AsyncMock(
        side_effect=discord.HTTPException(resp_mock, "service unavailable")
    )

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    with patch("lyra.adapters.discord.discord_inbound._pipeline", mock_pipeline):
        await handle_audio(adapter, message, attachment, TrustLevel.PUBLIC)

    # (a) Reply was sent
    message.reply.assert_awaited_once()
    reply_text: str = message.reply.call_args.args[0]
    assert (
        "audio" in reply_text.lower()
        or "retrieve" in reply_text.lower()
        or "try again" in reply_text.lower()
    )

    # (b) Pipeline was NOT reached — load-bearing assertion
    mock_pipeline.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# T4-5: invalid magic bytes early return — pipeline never awaited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_invalid_magic_reply_no_pipeline() -> None:
    """When audio bytes fail the magic-byte check, handler must:
    (a) await message.reply() with the audio_invalid_format text, and
    (b) NOT await discord_inbound._pipeline.run.

    Negative-test contract: deleting the early `return` after the magic-byte check
    would cause _pipeline.run to be awaited — assert_not_awaited() catches it.
    """
    adapter = _make_adapter()
    message = _make_discord_message()
    attachment = _make_audio_attachment()
    # Return bytes that look like plain text — fails every magic signature
    attachment.read = AsyncMock(return_value=b"NOTAUDIO" + b"\x00" * 20)

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    with patch("lyra.adapters.discord.discord_inbound._pipeline", mock_pipeline):
        await handle_audio(adapter, message, attachment, TrustLevel.PUBLIC)

    # (a) Reply was sent
    message.reply.assert_awaited_once()
    reply_text: str = message.reply.call_args.args[0]
    assert (
        "audio" in reply_text.lower()
        or "valid" in reply_text.lower()
        or "format" in reply_text.lower()
    )

    # (b) Pipeline was NOT reached — load-bearing assertion
    mock_pipeline.run.assert_not_awaited()
