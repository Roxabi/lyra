"""Telegram voice ingest routing via InboundPipeline (#1537 / #1551).

Covers:
- handle_voice_message() routes via _pipeline.run() (not push_to_hub_guarded())
- _download_audio IS awaited eagerly in the handler; the already-fetched bytes are
  wrapped in a trivial FetchFn closure for AttachmentIngestStage.
- The message passed to the pipeline carries a non-None pending_attachment.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.adapters.telegram import TelegramAdapter
from factory.adapters.telegram.telegram_inbound import handle_voice_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> TelegramAdapter:
    """Minimal TelegramAdapter with mocked bot — no real HTTP."""
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=MagicMock(),
    )
    bot_mock = AsyncMock()
    bot_mock.get_file = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def _make_voice_msg() -> SimpleNamespace:
    """Aiogram-like message carrying a voice note."""
    from datetime import datetime, timezone

    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=42, full_name="Alice", is_bot=False),
        message_thread_id=None,
        message_id=7,
        date=datetime.now(timezone.utc),
        voice=SimpleNamespace(file_id="voice_file_001", duration=5),
        audio=None,
        video_note=None,
        reply_to_message=None,
    )


# ---------------------------------------------------------------------------
# T3-1: handle_voice_message routes via _pipeline.run (not push_to_hub_guarded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_voice_routes_via_pipeline_not_direct_push(
    tmp_path: Path,
) -> None:
    """handle_voice_message() must await _pipeline.run() exactly once.

    The voice path now routes through InboundPipeline instead of calling
    push_to_hub_guarded() directly.
    """
    adapter = _make_adapter()
    msg = _make_voice_msg()

    audio_file = tmp_path / "voice.ogg"
    audio_file.write_bytes(b"fake_ogg")

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    with (
        patch("factory.adapters.telegram.telegram_inbound._pipeline", mock_pipeline),
        patch(
            "factory.adapters.telegram.telegram_inbound._download_audio",
            new_callable=AsyncMock,
            return_value=(audio_file, 5.0),
        ),
    ):
        await handle_voice_message(adapter, msg)

    # Assert: pipeline.run was called exactly once (routing contract)
    mock_pipeline.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# T3-2: _download_audio IS called eagerly; pipeline receives pending_attachment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_eager_download_and_pending_attachment_set(
    tmp_path: Path,
) -> None:
    """handle_voice_message() calls _download_audio eagerly (before pipeline.run).

    The already-fetched bytes are wrapped in a trivial FetchFn and stored in
    PendingAttachment so AttachmentIngestStage can call store.put() when a store
    is present.  The InboundMessage passed to the pipeline carries a non-None
    pending_attachment.
    """
    adapter = _make_adapter()
    msg = _make_voice_msg()

    audio_file = tmp_path / "voice.ogg"
    audio_file.write_bytes(b"audio_bytes")

    mock_pipeline = MagicMock()

    captured_msgs: list = []

    async def _capture_run(raw, ctx, parser, **kwargs):  # type: ignore[no-untyped-def]
        captured_msgs.append(raw)

    mock_pipeline.run = _capture_run

    with (
        patch("factory.adapters.telegram.telegram_inbound._pipeline", mock_pipeline),
        patch(
            "factory.adapters.telegram.telegram_inbound._download_audio",
            new_callable=AsyncMock,
            return_value=(audio_file, 5.0),
        ) as mock_download,
    ):
        await handle_voice_message(adapter, msg)

    # _download_audio must have been called eagerly by the handler
    mock_download.assert_awaited_once()

    # The message passed to the pipeline must carry a non-None pending_attachment
    assert len(captured_msgs) == 1
    routed_msg = captured_msgs[0]
    assert routed_msg.pending_attachment is not None


# ---------------------------------------------------------------------------
# T3-3: too-large early return — pipeline never awaited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_too_large_reply_no_pipeline(
    tmp_path: Path,
) -> None:
    """When _download_audio raises ValueError (file too large), handler must:
    (a) send the audio_too_large reply via adapter.bot.send_message, and
    (b) NOT await _pipeline.run — no routing to the pipeline.

    Negative-test contract: if the early `return` after the ValueError branch
    were deleted, _pipeline.run would be awaited and assert_not_awaited() fails.
    """
    adapter = _make_adapter()
    msg = _make_voice_msg()

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    with (
        patch("factory.adapters.telegram.telegram_inbound._pipeline", mock_pipeline),
        patch(
            "factory.adapters.telegram.telegram_inbound._download_audio",
            new_callable=AsyncMock,
            side_effect=ValueError("file too large"),
        ),
    ):
        await handle_voice_message(adapter, msg)

    # (a) Reply was sent — bot.send_message must have been called
    adapter.bot.send_message.assert_called_once()
    call_kwargs = adapter.bot.send_message.call_args
    # _make_send_kwargs passes text= as a keyword argument
    text_sent: str = call_kwargs.kwargs["text"]
    assert "large" in text_sent.lower()

    # (b) Pipeline was NOT reached — load-bearing assertion
    mock_pipeline.run.assert_not_awaited()


# ---------------------------------------------------------------------------
# T3-4: download failure early return — pipeline never awaited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tg_download_failed_reply_no_pipeline(
    tmp_path: Path,
) -> None:
    """When _download_audio raises a non-ValueError exception, handler must:
    (a) send the audio_download_failed reply via adapter.bot.send_message, and
    (b) NOT await _pipeline.run.

    Negative-test contract: if the early `return` after the except-Exception branch
    were deleted, _pipeline.run would be awaited and assert_not_awaited() fails.
    """
    adapter = _make_adapter()
    msg = _make_voice_msg()

    mock_pipeline = MagicMock()
    mock_pipeline.run = AsyncMock(return_value=None)

    with (
        patch("factory.adapters.telegram.telegram_inbound._pipeline", mock_pipeline),
        patch(
            "factory.adapters.telegram.telegram_inbound._download_audio",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection reset"),
        ),
    ):
        await handle_voice_message(adapter, msg)

    # (a) Reply was sent — bot.send_message must have been called
    adapter.bot.send_message.assert_called_once()
    call_kwargs = adapter.bot.send_message.call_args
    # _make_send_kwargs passes text= as a keyword argument
    text_sent: str = call_kwargs.kwargs["text"]
    # The audio_download_failed fallback contains "retrieve" or "try again"
    assert (
        "audio" in text_sent.lower()
        or "retrieve" in text_sent.lower()
        or "try again" in text_sent.lower()
    )

    # (b) Pipeline was NOT reached — load-bearing assertion
    mock_pipeline.run.assert_not_awaited()
