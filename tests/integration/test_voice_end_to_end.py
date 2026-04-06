"""End-to-end integration test: legacy NATS subject → compat shim → SttMiddleware.

Agent reply path.

Tests that the three pieces introduced in #534 Slice 1 wire together correctly:
  1. InboundAudioLegacyHandler (compat shim)
  2. LocalBus.inject() public API
  3. SttMiddleware wired into build_default_pipeline() → MiddlewarePipeline.process()

No real NATS server is needed.  The test drives the compat shim by calling
_on_message() directly with a mock NATS message, then pumps the pipeline
manually.

Mocked:
  - NATS transport (mock NATS message object)
  - STT service (FakeSTT returning a fixed transcript)
  - Hub (MagicMock exposing _stt, _msg_manager, dispatch_response + all attrs
    required by the middleware chain up to and including SttMiddleware)

Real (not mocked):
  - InboundAudioLegacyHandler (shim under test)
  - LocalBus.inject() (wired to the shim)
  - SttMiddleware.__call__() (consumes the injected message)
  - InboundAudio → InboundMessage conversion logic
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.audio_payload import AudioPayload
from lyra.core.inbound_bus import LocalBus
from lyra.core.message import InboundAudio, InboundMessage
from lyra.core.trust import TrustLevel
from lyra.nats.compat.inbound_audio_legacy import InboundAudioLegacyHandler

# ---------------------------------------------------------------------------
# Helpers / stubs shared across this module
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_FIXED_TRANSCRIPT = "hello from voice"


def _make_legacy_audio(  # noqa: PLR0913
    audio_id: str = "audio-e2e",
    platform: str = "telegram",
    bot_id: str = "testbot",
    scope_id: str = "chat:99",
    user_id: str = "user-e2e",
    user_name: str = "E2E User",
) -> InboundAudio:
    return InboundAudio(
        id=audio_id,
        platform=platform,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=user_name,
        is_mention=False,
        audio_bytes=b"fake-ogg-e2e",
        mime_type="audio/ogg",
        duration_ms=2000,
        file_id="file-e2e",
        timestamp=_NOW,
        trust_level=TrustLevel.PUBLIC,
        platform_meta={"chat_id": 99},
    )


def _serialize_to_nats_bytes(audio: InboundAudio) -> bytes:
    """Serialize InboundAudio using the same wire conventions as legacy adapters."""
    d = asdict(audio)
    if "timestamp" in d and isinstance(d["timestamp"], datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    if "audio_bytes" in d and isinstance(d.get("audio_bytes"), (bytes, bytearray)):
        d["audio_bytes"] = list(audio.audio_bytes)
    if "trust_level" in d:
        d["trust_level"] = audio.trust_level.value
    return json.dumps(d).encode()


def _make_nats_msg(data: bytes) -> MagicMock:
    msg = MagicMock()
    msg.data = data
    return msg


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
# T26 — end-to-end test (updated for SttMiddleware / MiddlewarePipeline)
# ---------------------------------------------------------------------------


class TestLegacySubjectVoiceMessageReachesAgentViaCompatShim:
    """
    Legacy InboundAudio JSON on lyra.inbound.audio.> → compat shim
    → LocalBus.inject() → SttMiddleware.__call__() → transcript.
    """

    @pytest.mark.asyncio()
    async def test_legacy_subject_voice_message_reaches_agent_via_compat_shim(
        self,
    ) -> None:
        # ------------------------------------------------------------------
        # Arrange — inbound_bus (real LocalBus), compat shim
        # ------------------------------------------------------------------
        inbound_bus: LocalBus[InboundMessage] = LocalBus(name="inbound-e2e")
        stt = _FakeSTT(transcript=_FIXED_TRANSCRIPT)
        hub = _make_hub_mock(stt=stt)

        handler = InboundAudioLegacyHandler(inbound_bus=inbound_bus)

        # Build legacy audio and wire it to a mock NATS message
        legacy_audio = _make_legacy_audio()
        nats_msg = _make_nats_msg(_serialize_to_nats_bytes(legacy_audio))

        # ------------------------------------------------------------------
        # Act 1 — compat shim receives legacy NATS message
        # ------------------------------------------------------------------
        await handler._on_message(nats_msg)

        # ------------------------------------------------------------------
        # Assert 1 — shim injected one voice InboundMessage into the bus
        # ------------------------------------------------------------------
        assert handler.converted_total == 1
        assert handler.decode_errors_total == 0

        # Retrieve the injected message from the bus staging queue
        injected_msg: InboundMessage = inbound_bus._staging.get_nowait()

        assert isinstance(injected_msg, InboundMessage)
        assert injected_msg.modality == "voice"
        assert injected_msg.audio is not None
        assert isinstance(injected_msg.audio, AudioPayload)
        assert injected_msg.audio.audio_bytes == legacy_audio.audio_bytes
        assert injected_msg.id == legacy_audio.id
        assert injected_msg.platform == legacy_audio.platform
        assert injected_msg.bot_id == legacy_audio.bot_id
        assert injected_msg.user_id == legacy_audio.user_id

        # text is empty before STT stage
        assert injected_msg.text == ""

        # ------------------------------------------------------------------
        # Act 2 — SttMiddleware processes the injected voice message
        # ------------------------------------------------------------------
        from lyra.core.hub.middleware import PipelineContext
        from lyra.core.hub.middleware_stt import SttMiddleware

        ctx = PipelineContext(hub=hub)

        captured_msgs: list[InboundMessage] = []

        async def _capture_next(
            updated_msg: InboundMessage, _ctx: PipelineContext
        ) -> Any:
            captured_msgs.append(updated_msg)
            from lyra.core.hub.message_pipeline import _DROP

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
