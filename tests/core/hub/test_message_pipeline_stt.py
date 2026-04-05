"""Tests for MessagePipeline._run_stt_stage (issue #534 Slice 1).

These tests target the NEW _run_stt_stage method which does NOT exist yet.
All tests fail at collection or at import time until T13 (implement
_run_stt_stage) and T5 (add AudioPayload) land — this is the expected RED
state.

The method signature expected:
    async def _run_stt_stage(
        self, msg: InboundMessage
    ) -> tuple[InboundMessage, PipelineResult | None]

Return semantics:
    (msg, None)           — continue pipeline
    (msg, PipelineResult) — terminal: pipeline stops, error reply dispatched
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from lyra.core.hub.message_pipeline import Action, MessagePipeline
from tests.helpers.messages import make_text_message, make_voice_message

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

MAX_TRANSCRIPT_LEN = 2000  # mirrors audio_pipeline.py constant


@dataclass
class FakeTranscription:
    text: str
    language: str = "en"
    duration_seconds: float = 2.5


class FakeSTT:
    """Minimal STT stub that returns a canned transcript."""

    def __init__(self, text: str = "Hello world", timeout_ms: int = 30000) -> None:
        self._text = text
        self.timeout_ms = timeout_ms

    async def transcribe(self, path: Any) -> FakeTranscription:
        return FakeTranscription(text=self._text)


class SlowSTT:
    """STT that sleeps indefinitely — for timeout testing."""

    timeout_ms: int = 30000

    async def transcribe(self, path: Any) -> FakeTranscription:
        await asyncio.sleep(9999)
        return FakeTranscription(text="never")


class NoisySTT:
    """STT that returns a transcript detected as whisper noise."""

    timeout_ms: int = 30000

    async def transcribe(self, path: Any) -> FakeTranscription:
        # is_whisper_noise() returns True for empty/noise transcripts
        return FakeTranscription(text="")


class UnavailableSTT:
    """STT that raises STTUnavailableError."""

    timeout_ms: int = 30000

    async def transcribe(self, path: Any) -> FakeTranscription:
        from lyra.stt import STTUnavailableError

        raise STTUnavailableError("model not loaded")


class ExplodingSTT:
    """STT that raises an unexpected generic exception."""

    timeout_ms: int = 30000

    async def transcribe(self, path: Any) -> FakeTranscription:
        raise RuntimeError("GPU exploded")


def _make_hub(stt: Any = None) -> MagicMock:
    """Build a minimal Hub mock for pipeline isolation tests."""
    hub = MagicMock()
    hub._stt = stt
    hub._msg_manager = MagicMock()
    hub._msg_manager.get = MagicMock(
        side_effect=lambda key: f"[{key} template]"
    )
    hub.dispatch_response = AsyncMock()
    return hub


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestRunSttStage:
    """Unit tests for MessagePipeline._run_stt_stage."""

    # ------------------------------------------------------------------
    # 1. Modality skip — text message passes through untouched
    # ------------------------------------------------------------------

    async def test_stt_stage_skips_if_modality_not_voice(self) -> None:
        # Arrange
        hub = _make_hub(stt=FakeSTT())
        pipeline = MessagePipeline(hub)
        msg = make_text_message()

        # Act
        returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert — continue sentinel, message unchanged
        assert result is None
        assert returned_msg is msg
        hub.dispatch_response.assert_not_called()

    # ------------------------------------------------------------------
    # 2. Re-entrance guard — voice message with text already populated
    # ------------------------------------------------------------------

    async def test_stt_stage_skips_if_text_already_populated(self) -> None:
        # Arrange
        hub = _make_hub(stt=FakeSTT())
        pipeline = MessagePipeline(hub)
        msg = make_voice_message(text="already transcribed")

        # Act
        returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert — no STT call, pipeline continues
        assert result is None
        hub.dispatch_response.assert_not_called()

    # ------------------------------------------------------------------
    # 3. hub._stt is None → stt_unsupported
    # ------------------------------------------------------------------

    async def test_stt_stage_unsupported_when_stt_none(self) -> None:
        # Arrange
        hub = _make_hub(stt=None)
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act
        returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert — terminal result, dispatch called with stt_unsupported template
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        content = response.content
        assert "stt_unsupported" in content or "[stt_unsupported" in content

    # ------------------------------------------------------------------
    # 4. STTUnavailableError → stt_unavailable
    # ------------------------------------------------------------------

    async def test_stt_stage_unavailable_when_STTUnavailableError(self) -> None:
        # Arrange
        hub = _make_hub(stt=UnavailableSTT())
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act
        returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        content = response.content
        assert "stt_unavailable" in content or "[stt_unavailable" in content

    # ------------------------------------------------------------------
    # 5. Whisper noise → stt_noise
    # ------------------------------------------------------------------

    async def test_stt_stage_noise_when_is_whisper_noise(self) -> None:
        # Arrange — is_whisper_noise returns True for empty/noise text
        hub = _make_hub(stt=NoisySTT())
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act
        with patch("lyra.stt.is_whisper_noise", return_value=True):
            returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_noise" in response.content or "[stt_noise" in response.content

    # ------------------------------------------------------------------
    # 6. Transcript starts with "/" → stt_invalid
    # ------------------------------------------------------------------

    async def test_stt_stage_invalid_when_slash_prefix(self) -> None:
        # Arrange
        stt = FakeSTT(text="/start inject")
        hub = _make_hub(stt=stt)
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act
        with patch("lyra.stt.is_whisper_noise", return_value=False):
            returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_invalid" in response.content or "[stt_invalid" in response.content

    # ------------------------------------------------------------------
    # 7. Transcript exceeds MAX_TRANSCRIPT_LEN → stt_invalid
    # ------------------------------------------------------------------

    async def test_stt_stage_invalid_when_over_length(self) -> None:
        # Arrange — transcript is longer than the hard cap
        long_text = "x" * (MAX_TRANSCRIPT_LEN + 1)
        stt = FakeSTT(text=long_text)
        hub = _make_hub(stt=stt)
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act
        with patch("lyra.stt.is_whisper_noise", return_value=False):
            returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert — transcript over cap triggers stt_invalid
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_invalid" in response.content or "[stt_invalid" in response.content

    # ------------------------------------------------------------------
    # 8. Unexpected generic exception → stt_failed
    # ------------------------------------------------------------------

    async def test_stt_stage_failed_on_unexpected_exception(self) -> None:
        # Arrange
        hub = _make_hub(stt=ExplodingSTT())
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act
        returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_failed" in response.content or "[stt_failed" in response.content

    # ------------------------------------------------------------------
    # 9. Timeout → stt_failed (within bounded wall-clock time)
    # ------------------------------------------------------------------

    async def test_stt_stage_timeout_dispatches_stt_failed(self) -> None:
        # Arrange — STT sleeps forever, stage must time out via asyncio.wait_for
        hub = _make_hub(stt=SlowSTT())
        hub._stt.timeout_ms = 100  # override to 100ms for fast test
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()

        # Act — bound to 35s per spec, but effectively 200ms here
        returned_msg, result = await asyncio.wait_for(
            pipeline._run_stt_stage(msg), timeout=35.0
        )

        # Assert — timeout is caught internally and routed to stt_failed
        assert result is not None
        assert result.action == Action.DROP
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_failed" in response.content or "[stt_failed" in response.content

    # ------------------------------------------------------------------
    # 10. Happy path: success → populate text, strip audio, echo transcript
    # ------------------------------------------------------------------

    async def test_stt_stage_success_populates_text_strips_audio_echoes_transcript(
        self,
    ) -> None:
        # Arrange
        transcript = "hello from voice"
        stt = FakeSTT(text=transcript)
        hub = _make_hub(stt=stt)
        pipeline = MessagePipeline(hub)
        msg = make_voice_message()
        assert msg.audio is not None  # pre-condition: audio is present

        # Act
        with patch("lyra.stt.is_whisper_noise", return_value=False):
            returned_msg, result = await pipeline._run_stt_stage(msg)

        # Assert — pipeline continues (None result)
        assert result is None

        # Text populated from transcript
        assert returned_msg.text == transcript

        # text_raw carries the emoji prefix
        assert returned_msg.text_raw == f"\U0001f3a4 [voice]: {transcript}"

        # Audio stripped after successful transcription
        assert returned_msg.audio is None

        # Language propagated from STT result
        assert returned_msg.language == "en"

        # Transcript echo dispatched (informational, not a direct reply)
        hub.dispatch_response.assert_called_once()
