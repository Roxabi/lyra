"""Tests for SttMiddleware.__call__ (issue #534).

Ported from the original MessagePipeline._run_stt_stage tests.  The new
contract is:

    result = await SttMiddleware()(msg, ctx, next_fn)

Pass-through / success paths: ``next_fn`` is called once with the (possibly
updated) message.  Error / drop paths: ``next_fn`` is NOT called and the
return value is ``_DROP``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.hub.message_pipeline import _DROP, Action, PipelineResult
from lyra.core.hub.middleware import PipelineContext
from lyra.core.hub.middleware_stt import SttMiddleware
from tests.helpers.messages import make_text_message, make_voice_message

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

MAX_TRANSCRIPT_LEN = 2000  # mirrors middleware_stt.py constant


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
    """STT that raises STTNoiseError (noise detection owned by the adapter)."""

    timeout_ms: int = 30000

    async def transcribe(self, path: Any) -> FakeTranscription:
        from lyra.stt import STTNoiseError

        raise STTNoiseError("Noise transcript: ''")


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
    """Build a minimal Hub mock for SttMiddleware isolation tests."""
    hub = MagicMock()
    hub._stt = stt
    hub._msg_manager = MagicMock()
    hub._msg_manager.get = MagicMock(side_effect=lambda key: f"[{key} template]")
    hub.dispatch_response = AsyncMock()
    return hub


def _make_ctx(hub: MagicMock) -> PipelineContext:
    """Build a minimal PipelineContext backed by the given hub mock."""
    return PipelineContext(hub=hub)


_SENTINEL_RESULT = PipelineResult(action=Action.SUBMIT_TO_POOL)


async def _next_fn_passthrough(msg: Any, ctx: Any) -> PipelineResult:
    """Default next_fn — returns a sentinel so callers can verify it was invoked."""
    return _SENTINEL_RESULT


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestSttMiddleware:
    """Unit tests for SttMiddleware.__call__."""

    # ------------------------------------------------------------------
    # 1. Modality skip — text message passes through untouched
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_skips_if_modality_not_voice(self) -> None:
        # Arrange
        hub = _make_hub(stt=FakeSTT())
        ctx = _make_ctx(hub)
        msg = make_text_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert — next called with the original message, no dispatch
        next_fn.assert_called_once_with(msg, ctx)
        assert result is _SENTINEL_RESULT
        hub.dispatch_response.assert_not_called()

    # ------------------------------------------------------------------
    # 2. Re-entrance guard — voice message with text already populated
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_skips_if_text_already_populated(self) -> None:
        # Arrange
        hub = _make_hub(stt=FakeSTT())
        ctx = _make_ctx(hub)
        msg = make_voice_message(text="already transcribed")
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert — next called, no STT, no dispatch
        next_fn.assert_called_once_with(msg, ctx)
        assert result is _SENTINEL_RESULT
        hub.dispatch_response.assert_not_called()

    # ------------------------------------------------------------------
    # 3. hub._stt is None → stt_unsupported, drop
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_unsupported_when_stt_none(self) -> None:
        # Arrange
        hub = _make_hub(stt=None)
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert — dropped, next not called
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        content = response.content
        assert "stt_unsupported" in content or "[stt_unsupported" in content

    # ------------------------------------------------------------------
    # 4. STTUnavailableError → stt_unavailable, drop
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_unavailable_when_STTUnavailableError(self) -> None:
        # Arrange
        hub = _make_hub(stt=UnavailableSTT())
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        content = response.content
        assert "stt_unavailable" in content or "[stt_unavailable" in content

    # ------------------------------------------------------------------
    # 5. Whisper noise → stt_noise, drop
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_noise_when_is_whisper_noise(self) -> None:
        # Arrange
        hub = _make_hub(stt=NoisySTT())
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_noise" in response.content or "[stt_noise" in response.content

    # ------------------------------------------------------------------
    # 6. Transcript starts with "/" → stt_invalid, drop
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_invalid_when_slash_prefix(self) -> None:
        # Arrange
        stt = FakeSTT(text="/start inject")
        hub = _make_hub(stt=stt)
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_invalid" in response.content or "[stt_invalid" in response.content

    # ------------------------------------------------------------------
    # 7. Transcript exceeds MAX_TRANSCRIPT_LEN → stt_invalid, drop
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_invalid_when_over_length(self) -> None:
        # Arrange — transcript is longer than the hard cap
        long_text = "x" * (MAX_TRANSCRIPT_LEN + 1)
        stt = FakeSTT(text=long_text)
        hub = _make_hub(stt=stt)
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert — transcript over cap triggers stt_invalid
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_invalid" in response.content or "[stt_invalid" in response.content

    # ------------------------------------------------------------------
    # 8. Unexpected generic exception → stt_failed, drop
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_failed_on_unexpected_exception(self) -> None:
        # Arrange
        hub = _make_hub(stt=ExplodingSTT())
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_failed" in response.content or "[stt_failed" in response.content

    # ------------------------------------------------------------------
    # 9. Timeout → stt_failed, drop (within bounded wall-clock time)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_timeout_dispatches_stt_failed(self) -> None:
        # Arrange — STT sleeps forever; stage times out via asyncio.wait_for
        hub = _make_hub(stt=SlowSTT())
        hub._stt.timeout_ms = 100  # override to 100ms for fast test
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act — bound to 35s per spec, effectively ~200ms here
        result = await asyncio.wait_for(
            SttMiddleware()(msg, ctx, next_fn), timeout=35.0
        )

        # Assert — timeout caught internally, routed to stt_failed
        assert result == _DROP
        assert result.action == Action.DROP
        next_fn.assert_not_called()
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert "stt_failed" in response.content or "[stt_failed" in response.content

    # ------------------------------------------------------------------
    # 10. Happy path: success → populate text, strip audio, echo transcript
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_success_populates_text_strips_audio_echoes_transcript(
        self,
    ) -> None:
        # Arrange
        transcript = "hello from voice"
        stt = FakeSTT(text=transcript)
        hub = _make_hub(stt=stt)
        ctx = _make_ctx(hub)
        msg = make_voice_message()
        assert msg.audio is not None  # pre-condition: audio is present

        captured_msgs: list[Any] = []

        async def _capture_next(updated_msg: Any, _ctx: Any) -> PipelineResult:
            captured_msgs.append(updated_msg)
            return _SENTINEL_RESULT

        # Act
        result = await SttMiddleware()(msg, ctx, _capture_next)

        # Assert — pipeline continues (next called, sentinel returned)
        assert result is _SENTINEL_RESULT
        assert len(captured_msgs) == 1
        returned_msg = captured_msgs[0]

        # Text populated from transcript
        assert returned_msg.text == transcript

        # text_raw carries the emoji prefix
        assert returned_msg.text_raw == f"\U0001f3a4 {transcript}"

        # Audio stripped after successful transcription
        assert returned_msg.audio is None

        # Language propagated from STT result
        assert returned_msg.language == "en"

        # Transcript echo dispatched (informational, not a direct reply)
        hub.dispatch_response.assert_called_once()

    # ------------------------------------------------------------------
    # 11. modality=None passes through (non-voice, non-text modality)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio()
    async def test_stt_stage_skips_if_modality_none(self) -> None:
        # Arrange — message with no modality (e.g. a future modality type)
        hub = _make_hub(stt=FakeSTT())
        ctx = _make_ctx(hub)
        msg = make_text_message(modality=None)
        next_fn = AsyncMock(return_value=_SENTINEL_RESULT)

        # Act
        result = await SttMiddleware()(msg, ctx, next_fn)

        # Assert — not a voice message, passes through without STT
        next_fn.assert_called_once_with(msg, ctx)
        assert result is _SENTINEL_RESULT
        hub.dispatch_response.assert_not_called()
