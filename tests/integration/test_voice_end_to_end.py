"""End-to-end integration test: adapter publishes InboundMessage(voice) → SttMiddleware.

Slice 2 (issue #534): the compat shim (InboundAudioLegacyHandler) is deleted.
Adapters now produce InboundMessage(modality="voice", audio=AudioPayload(...))
directly and publish to the unified lyra.inbound.<platform>.<bot_id> subject.

Tests that the Slice 2 wire-up works correctly:
  1. Adapter-style InboundMessage(modality="voice") injected directly into LocalBus
  2. SttMiddleware processes the voice message
  3. msg.text is populated from the STT transcript
  4. msg.audio is None after the STT stage

No real NATS server is needed.  The test injects the message directly into a
LocalBus (the same bus the hub uses internally), then drives SttMiddleware manually.

Mocked:
  - STT service (FakeSTT returning a fixed transcript)
  - Hub (MagicMock exposing _stt, _msg_manager, dispatch_response + all attrs
    required by the middleware chain up to and including SttMiddleware)

Real (not mocked):
  - LocalBus.inject() (unified inbound bus)
  - SttMiddleware.__call__() (consumes the injected voice message)
  - InboundMessage + AudioPayload (Slice 2 unified message model)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.audio_payload import AudioPayload
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.inbound_bus import LocalBus
from lyra.core.messaging.message import InboundMessage

# ---------------------------------------------------------------------------
# Helpers / stubs shared across this module
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_TRANSCRIPT = "hello from voice"


def _make_voice_message(  # noqa: PLR0913
    audio_id: str = "audio-e2e",
    platform: str = "telegram",
    bot_id: str = "testbot",
    scope_id: str = "chat:99",
    user_id: str = "user-e2e",
    user_name: str = "E2E User",
) -> InboundMessage:
    """Build a minimal InboundMessage(modality='voice') as Slice 2 adapters produce."""
    return InboundMessage(
        id=audio_id,
        platform=platform,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=user_name,
        is_mention=False,
        text="",
        text_raw="",
        timestamp=_NOW,
        platform_meta={"chat_id": 99},
        trust_level=TrustLevel.PUBLIC,
        modality="voice",
        audio=AudioPayload(
            audio_bytes=b"fake-ogg-e2e",
            mime_type="audio/ogg",
            duration_ms=2000,
            file_id="file-e2e",
        ),
    )


class _FakeTranscription:
    def __init__(self, text: str, language: str = "en") -> None:
        self.text = text
        self.language = language
        self.duration_seconds = 1.5
        self.timeout_ms = 30000


class _FakeSTT:
    timeout_ms: int = 30000

    def __init__(self, transcript: str = _FIXED_TRANSCRIPT) -> None:
        self._transcript = transcript

    async def transcribe(self, path: Any) -> _FakeTranscription:
        return _FakeTranscription(text=self._transcript, language="en")


def _make_hub_mock(stt: Any = None) -> MagicMock:
    """Build a minimal Hub mock that satisfies SttMiddleware + surrounding guards.

    The MiddlewarePipeline runs TraceMiddleware, ValidatePlatformMiddleware,
    ResolveTrustMiddleware, TrustGuardMiddleware, RateLimitMiddleware, then
    SttMiddleware.  We let the earlier stages pass through by ensuring the hub
    mock returns permissive values for their look-ups, but the pipeline will
    DROP after SttMiddleware because ResolveBindingMiddleware will find no
    binding (hub.resolve_binding returns MagicMock's default falsy-ish value).

    For this test we only care that SttMiddleware ran and populated the message.
    We verify via dispatch_response (the echo call that SttMiddleware makes on
    success) and by capturing the updated message via a patched next step.
    """
    hub = MagicMock()
    hub._stt = stt
    hub._msg_manager = MagicMock()
    hub._msg_manager.get = MagicMock(side_effect=lambda key: f"[{key} template]")
    hub.dispatch_response = AsyncMock()
    return hub


# ---------------------------------------------------------------------------
# T16 — Slice 2 end-to-end test
# ---------------------------------------------------------------------------


class TestSlice2VoiceMessageReachesSTTMiddleware:
    """
    InboundMessage(modality='voice') injected into LocalBus
    → SttMiddleware populates text and clears audio.
    """

    @pytest.mark.asyncio()
    async def test_voice_message_stt_populates_text_and_clears_audio(
        self,
    ) -> None:
        # ------------------------------------------------------------------
        # Arrange — inbound_bus (real LocalBus), voice message, mock hub
        # ------------------------------------------------------------------
        inbound_bus: LocalBus[InboundMessage] = LocalBus(name="inbound-e2e")
        stt = _FakeSTT(transcript=_FIXED_TRANSCRIPT)
        hub = _make_hub_mock(stt=stt)

        voice_msg = _make_voice_message()

        # ------------------------------------------------------------------
        # Act 1 — inject voice message directly (Slice 2: no compat shim)
        # ------------------------------------------------------------------
        inbound_bus.inject(voice_msg)

        # ------------------------------------------------------------------
        # Assert 1 — message is in the bus staging queue
        # ------------------------------------------------------------------
        injected_msg: InboundMessage = inbound_bus._staging.get_nowait()

        assert isinstance(injected_msg, InboundMessage)
        assert injected_msg.modality == "voice"
        assert injected_msg.audio is not None
        assert isinstance(injected_msg.audio, AudioPayload)
        assert voice_msg.audio is not None  # always set by _make_voice_message()
        assert injected_msg.audio.audio_bytes == voice_msg.audio.audio_bytes
        assert injected_msg.id == voice_msg.id
        assert injected_msg.platform == voice_msg.platform
        assert injected_msg.bot_id == voice_msg.bot_id
        assert injected_msg.user_id == voice_msg.user_id

        # text is empty before STT stage
        assert injected_msg.text == ""

        # ------------------------------------------------------------------
        # Act 2 — SttMiddleware processes the voice message
        # ------------------------------------------------------------------
        from lyra.core.hub.middleware import PipelineContext
        from lyra.core.hub.middleware.middleware_stt import SttMiddleware

        ctx = PipelineContext(hub=hub)

        captured_msgs: list[InboundMessage] = []

        async def _capture_next(
            updated_msg: InboundMessage, _ctx: PipelineContext
        ) -> Any:
            captured_msgs.append(updated_msg)
            from lyra.core.hub.pipeline.message_pipeline import _DROP

            return _DROP

        with patch("lyra.stt.is_whisper_noise", return_value=False):
            await SttMiddleware()(injected_msg, ctx, _capture_next)

        # ------------------------------------------------------------------
        # Assert 2 — SttMiddleware populated text, stripped audio, echoed reply
        # ------------------------------------------------------------------

        # next was called with the updated message
        assert len(captured_msgs) == 1
        updated_msg = captured_msgs[0]

        # Text populated from transcript
        assert updated_msg.text == _FIXED_TRANSCRIPT

        # text_raw carries the voice emoji prefix
        assert "\U0001f3a4" in updated_msg.text_raw

        # Audio stripped after successful transcription
        assert updated_msg.audio is None

        # Language propagated from STT result
        assert updated_msg.language == "en"

        # modality preserved as "voice" (the agent knows the origin was audio)
        assert updated_msg.modality == "voice"

        # Transcript echo was dispatched
        hub.dispatch_response.assert_called_once()
        _reply_msg, response = hub.dispatch_response.call_args[0]
        assert _FIXED_TRANSCRIPT in response.content

    @pytest.mark.asyncio()
    async def test_voice_message_stt_unavailable_drops_cleanly(self) -> None:
        """SttMiddleware drops voice message cleanly when hub._stt is None."""
        from lyra.core.hub.middleware import PipelineContext
        from lyra.core.hub.middleware.middleware_stt import SttMiddleware

        hub = _make_hub_mock(stt=None)  # STT not configured
        voice_msg = _make_voice_message()
        ctx = PipelineContext(hub=hub)

        next_called: list[bool] = []

        async def _capture_next(msg: InboundMessage, _ctx: PipelineContext) -> Any:
            next_called.append(True)
            from lyra.core.hub.pipeline.message_pipeline import _DROP

            return _DROP

        # Must not raise even though stt is None
        with patch("lyra.stt.is_whisper_noise", return_value=False):
            await SttMiddleware()(voice_msg, ctx, _capture_next)

        # Pipeline was dropped — next was not called with a populated message
        # (SttMiddleware drops when stt is None, so next is never reached)
        assert len(next_called) == 0

    @pytest.mark.asyncio()
    async def test_voice_message_stt_timeout_drops_cleanly(self) -> None:
        """SttMiddleware drops voice message cleanly when STT times out."""
        import asyncio

        from lyra.core.hub.middleware import PipelineContext
        from lyra.core.hub.middleware.middleware_stt import SttMiddleware

        class _TimeoutSTT:
            timeout_ms: int = 30000

            async def transcribe(self, path: Any) -> None:
                raise asyncio.TimeoutError

        hub = _make_hub_mock(stt=_TimeoutSTT())
        voice_msg = _make_voice_message()
        ctx = PipelineContext(hub=hub)

        next_called: list[bool] = []

        async def _capture_next(msg: InboundMessage, _ctx: PipelineContext) -> Any:
            next_called.append(True)
            from lyra.core.hub.pipeline.message_pipeline import _DROP

            return _DROP

        # Must not raise on timeout
        with patch("lyra.stt.is_whisper_noise", return_value=False):
            await SttMiddleware()(voice_msg, ctx, _capture_next)

        # Pipeline was dropped — no text populated, no crash
        assert len(next_called) == 0
