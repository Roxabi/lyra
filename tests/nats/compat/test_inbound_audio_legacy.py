"""Tests for InboundAudioLegacyHandler (issue #534 Slice 1, compat shim).

The module lyra.nats.compat.inbound_audio_legacy does NOT exist yet — it will
be created in T9+T10.  All tests fail at collection time with ModuleNotFoundError
until then — this is the expected RED state.

The handler is responsible for:
  1. Subscribing to lyra.inbound.audio.> (legacy subject tree, Phase 1 only).
  2. Version-checking each payload via check_schema_version().
  3. Deserializing InboundAudio JSON and converting to InboundMessage(modality="voice").
  4. Injecting the converted message via inbound_bus.inject() (new public API).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from lyra.core.message import AudioPayload, InboundAudio, InboundMessage
from lyra.core.trust import TrustLevel

# RED import — module does not exist yet (T9/T10 will create it)
from lyra.nats.compat.inbound_audio_legacy import InboundAudioLegacyHandler

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

SCHEMA_VERSION_INBOUND_AUDIO = 1


def _make_legacy_audio(  # noqa: PLR0913
    audio_id: str = "audio-1",
    platform: str = "telegram",
    bot_id: str = "testbot",
    scope_id: str = "chat:42",
    user_id: str = "user-1",
    user_name: str = "Test User",
    trust_level: TrustLevel = TrustLevel.PUBLIC,
    schema_version: int = SCHEMA_VERSION_INBOUND_AUDIO,
) -> InboundAudio:
    """Build a minimal InboundAudio for legacy handler tests."""
    return InboundAudio(
        id=audio_id,
        platform=platform,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id=user_id,
        user_name=user_name,
        is_mention=False,
        audio_bytes=b"fake-ogg",
        mime_type="audio/ogg",
        duration_ms=1500,
        file_id="file-1",
        timestamp=_NOW,
        trust_level=trust_level,
        schema_version=schema_version,
        platform_meta={"chat_id": 42},
    )


def _serialize_legacy(audio: InboundAudio) -> bytes:
    """Serialize an InboundAudio to JSON bytes, suitable for a NATS message."""
    import dataclasses

    d = dataclasses.asdict(audio)
    # datetime → ISO string
    if "timestamp" in d and isinstance(d["timestamp"], datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    # bytes → list[int] (matches serialize() conventions)
    if "audio_bytes" in d and isinstance(d.get("audio_bytes"), (bytes, bytearray)):
        d["audio_bytes"] = list(audio.audio_bytes)
    # trust_level → string value
    if "trust_level" in d:
        d["trust_level"] = audio.trust_level.value
    return json.dumps(d).encode()


def _make_nats_msg(data: bytes) -> MagicMock:
    """Minimal NATS Msg mock with a .data attribute."""
    msg = MagicMock()
    msg.data = data
    return msg


def _make_mock_bus() -> MagicMock:
    """Bus mock with a public inject() method."""
    bus = MagicMock()
    bus.inject = MagicMock()
    return bus


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboundAudioLegacyHandlerDecode:
    """Round-trip: valid InboundAudio JSON → inject(InboundMessage(voice))."""

    async def test_legacy_handler_decodes_and_injects(self) -> None:
        # Arrange
        audio = _make_legacy_audio()
        nats_msg = _make_nats_msg(_serialize_legacy(audio))
        bus = _make_mock_bus()
        handler = InboundAudioLegacyHandler(inbound_bus=bus)

        # Act
        await handler._on_message(nats_msg)

        # Assert — inject called once with a voice InboundMessage
        bus.inject.assert_called_once()
        injected: InboundMessage = bus.inject.call_args[0][0]
        assert isinstance(injected, InboundMessage)
        assert injected.modality == "voice"
        assert injected.audio is not None
        assert isinstance(injected.audio, AudioPayload)
        assert injected.audio.audio_bytes == audio.audio_bytes
        assert injected.audio.mime_type == audio.mime_type
        assert injected.platform == audio.platform
        assert injected.bot_id == audio.bot_id
        assert injected.user_id == audio.user_id

    async def test_legacy_handler_version_mismatch_drops_and_counts(self) -> None:
        # Arrange — schema_version=2 (future, unknown version)
        audio = _make_legacy_audio(schema_version=2)
        nats_msg = _make_nats_msg(_serialize_legacy(audio))
        bus = _make_mock_bus()
        handler = InboundAudioLegacyHandler(inbound_bus=bus)

        # Act
        await handler._on_message(nats_msg)

        # Assert — inject NOT called, decode error counter incremented
        bus.inject.assert_not_called()
        assert handler.decode_errors_total >= 1

    async def test_legacy_handler_decode_error_counts(self) -> None:
        # Arrange — malformed JSON payload
        nats_msg = _make_nats_msg(b"not-valid-json{{{")
        bus = _make_mock_bus()
        handler = InboundAudioLegacyHandler(inbound_bus=bus)

        # Act
        await handler._on_message(nats_msg)

        # Assert — inject NOT called, decode error counter incremented
        bus.inject.assert_not_called()
        assert handler.decode_errors_total >= 1


class TestInboundAudioLegacyConvert:
    """Unit test for the _convert_legacy static/class method."""

    def test_legacy_handler_converts_InboundAudio_to_InboundMessage_voice(
        self,
    ) -> None:
        # Arrange
        audio = _make_legacy_audio()

        # Act — _convert_legacy is a static/class method; call directly
        result: InboundMessage = InboundAudioLegacyHandler._convert_legacy(audio)

        # Assert — all relevant fields copied over
        assert isinstance(result, InboundMessage)
        assert result.modality == "voice"
        assert result.text == ""
        assert result.text_raw == ""
        assert result.platform == audio.platform
        assert result.bot_id == audio.bot_id
        assert result.scope_id == audio.scope_id
        assert result.user_id == audio.user_id
        assert result.user_name == audio.user_name
        assert result.is_mention == audio.is_mention
        assert result.timestamp == audio.timestamp
        assert result.trust_level == audio.trust_level
        assert result.platform_meta == audio.platform_meta
        assert result.routing == audio.routing

        # AudioPayload mirrors InboundAudio audio fields
        assert result.audio is not None
        assert isinstance(result.audio, AudioPayload)
        assert result.audio.audio_bytes == audio.audio_bytes
        assert result.audio.mime_type == audio.mime_type
        assert result.audio.duration_ms == audio.duration_ms
        assert result.audio.file_id == audio.file_id


class TestInboundAudioLegacyPublicAPI:
    """Handler must use bus.inject() public API, not _staging directly."""

    async def test_legacy_handler_injects_via_public_Bus_inject(self) -> None:
        # Arrange
        audio = _make_legacy_audio()
        nats_msg = _make_nats_msg(_serialize_legacy(audio))
        bus = _make_mock_bus()
        handler = InboundAudioLegacyHandler(inbound_bus=bus)

        # Act
        await handler._on_message(nats_msg)

        # Assert — public inject() called, not _staging (private access forbidden)
        bus.inject.assert_called_once()
        # Ensure _staging was NOT accessed (no private access via the handler)
        assert not hasattr(bus, "_staging") or not bus._staging.called
