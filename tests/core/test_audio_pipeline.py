"""Tests for AudioPipeline — covers uncovered branches from hub decomposition.

Targets: trust-level exit, rate-limit branch, slash-command injection guard,
transcript length cap, inbound bus full, per-agent TTS wiring.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_config import AgentTTSConfig
from lyra.core.hub import Hub
from lyra.core.message import InboundAudio, InboundMessage, Platform, Response
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio(
    audio_id: str = "audio-1",
    platform: str = "telegram",
    trust_level: TrustLevel = TrustLevel.TRUSTED,
    user_id: str = "alice",
) -> InboundAudio:
    return InboundAudio(
        id=audio_id,
        platform=platform,
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        audio_bytes=b"\x00" * 100,
        mime_type="audio/ogg",
        duration_ms=3000,
        file_id="file-1",
        timestamp=datetime.now(timezone.utc),
        trust_level=trust_level,
        user_name="Alice",
        platform_meta={"chat_id": 42, "is_group": False},
    )


@dataclass
class FakeTranscription:
    text: str
    language: str = "en"
    duration_seconds: float = 2.5


class FakeSTT:
    def __init__(self, text: str = "Hello world") -> None:
        self._text = text

    async def transcribe(self, path):
        return FakeTranscription(text=self._text)


class _DispatchCapture:
    """Captures dispatch_response calls and signals completion."""

    def __init__(self) -> None:
        self.dispatched: list[tuple[InboundMessage, Response]] = []
        self.done = asyncio.Event()

    async def __call__(self, msg, response):
        self.dispatched.append((msg, response))
        self.done.set()


# ---------------------------------------------------------------------------
# Trust Level
# ---------------------------------------------------------------------------


class TestAudioPipelineTrustLevel:
    """Audio with trust_level=BLOCKED must be silently dropped."""

    @pytest.mark.asyncio()
    async def test_blocked_audio_is_dropped(self):
        stt = FakeSTT()
        hub = Hub(stt=stt)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        capture = _DispatchCapture()
        hub.dispatch_response = capture  # type: ignore[assignment]

        # Blocked audio
        audio = _make_audio(trust_level=TrustLevel.BLOCKED)
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        # Followed by a normal audio to prove the loop didn't stall
        normal = _make_audio(audio_id="audio-2", trust_level=TrustLevel.TRUSTED)
        hub.inbound_audio_bus.put(Platform.TELEGRAM, normal)

        await hub.inbound_bus.start()
        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            # The normal audio should produce the echo reply
            msg: InboundMessage = await asyncio.wait_for(
                hub.inbound_bus._staging.get(), timeout=2.0
            )
            assert msg.id == "audio-2"
            # No dispatch for the blocked audio — capture should only have the
            # echo for the normal audio
            assert all(m.id == "audio-2" for m, _ in capture.dispatched)
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
            await hub.inbound_bus.stop()


# ---------------------------------------------------------------------------
# Per-agent TTS wiring (#280)
# ---------------------------------------------------------------------------


class TestSynthesizeDispatchAgentTTS:
    """synthesize_and_dispatch_audio forwards agent_tts to synthesize()."""

    @pytest.mark.asyncio()
    async def test_agent_tts_forwarded_to_synthesize(self):
        """When agent_tts is passed, it reaches TTSService.synthesize()."""
        from lyra.tts import SynthesisResult

        agent_tts = AgentTTSConfig(
            engine="agent_eng", voice="agent_vox"
        )

        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=SynthesisResult(
                audio_bytes=b"fake",
                mime_type="audio/ogg",
                duration_ms=100,
            )
        )

        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]
        hub._tts = mock_tts
        hub.dispatch_audio = AsyncMock()  # type: ignore[method-assign]

        msg = InboundMessage(
            id="msg-tts-1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        await hub._audio_pipeline.synthesize_and_dispatch_audio(
            msg, "Hello world", agent_tts=agent_tts
        )

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args
        assert call_kwargs.kwargs.get("agent_tts") is agent_tts

    @pytest.mark.asyncio()
    async def test_agent_tts_none_no_regression(self):
        """Without agent_tts, synthesize() is called without it."""
        from lyra.tts import SynthesisResult

        mock_tts = MagicMock()
        mock_tts.synthesize = AsyncMock(
            return_value=SynthesisResult(
                audio_bytes=b"fake",
                mime_type="audio/ogg",
                duration_ms=100,
            )
        )

        hub = Hub(stt=FakeSTT())  # type: ignore[arg-type]
        hub._tts = mock_tts
        hub.dispatch_audio = AsyncMock()  # type: ignore[method-assign]

        msg = InboundMessage(
            id="msg-tts-2",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
            user_id="bob",
            user_name="Bob",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
        )

        await hub._audio_pipeline.synthesize_and_dispatch_audio(
            msg, "Hello world"
        )

        mock_tts.synthesize.assert_awaited_once()
        call_kwargs = mock_tts.synthesize.call_args
        assert call_kwargs.kwargs.get("agent_tts") is None


# ---------------------------------------------------------------------------
# Rate Limit
# ---------------------------------------------------------------------------


class TestAudioPipelineRateLimit:
    """Audio exceeding per-user rate limit gets a rate_limited reply."""

    @pytest.mark.asyncio()
    async def test_rate_limited_audio_gets_reply(self):
        stt = FakeSTT()
        # rate_limit=1 means second audio from same user triggers limit
        hub = Hub(stt=stt, rate_limit=1, rate_window=60)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        capture = _DispatchCapture()
        hub.dispatch_response = capture  # type: ignore[assignment]

        # First audio consumes the rate allowance
        audio1 = _make_audio(audio_id="audio-1")
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio1)

        # Second audio should be rate-limited
        audio2 = _make_audio(audio_id="audio-2")
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio2)

        await hub.inbound_bus.start()
        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            # Wait for the first audio to be enqueued as text
            await asyncio.wait_for(hub.inbound_bus._staging.get(), timeout=2.0)
            # Give the loop time to process second audio and dispatch rate limit
            await asyncio.sleep(0.1)
            # The rate-limited reply should be dispatched
            rl_replies = [
                (m, r)
                for m, r in capture.dispatched
                if "too fast" in r.content.lower() or "slow down" in r.content.lower()
            ]
            assert len(rl_replies) == 1
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
            await hub.inbound_bus.stop()


# ---------------------------------------------------------------------------
# Slash-Command Injection Guard
# ---------------------------------------------------------------------------


class TestAudioPipelineSlashInjection:
    """Transcript starting with '/' must be rejected with stt_invalid reply."""

    @pytest.mark.parametrize("transcript", ["/start", "  /help", "/clear all"])
    @pytest.mark.asyncio()
    async def test_slash_transcript_rejected(self, transcript: str):
        stt = FakeSTT(text=transcript)
        hub = Hub(stt=stt)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        capture = _DispatchCapture()
        hub.dispatch_response = capture  # type: ignore[assignment]

        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            await asyncio.wait_for(capture.done.wait(), timeout=2.0)
            assert len(capture.dispatched) == 1
            _, resp = capture.dispatched[0]
            assert "couldn't process" in resp.content.lower()
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()


# ---------------------------------------------------------------------------
# Transcript Length Cap
# ---------------------------------------------------------------------------


class TestAudioPipelineTranscriptCap:
    """Transcript exceeding 2000 chars must be truncated."""

    @pytest.mark.asyncio()
    async def test_long_transcript_truncated(self):
        long_text = "a" * 3000
        stt = FakeSTT(text=long_text)
        hub = Hub(stt=stt)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        hub.dispatch_response = lambda msg, resp: asyncio.sleep(0)  # type: ignore[assignment]

        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        await hub.inbound_bus.start()
        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            msg: InboundMessage = await asyncio.wait_for(
                hub.inbound_bus._staging.get(), timeout=2.0
            )
            assert len(msg.text) == 2000
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
            await hub.inbound_bus.stop()


# ---------------------------------------------------------------------------
# Inbound Bus Full
# ---------------------------------------------------------------------------


class TestAudioPipelineBusFull:
    """When inbound bus is full, transcribed audio is dropped gracefully."""

    @pytest.mark.asyncio()
    async def test_bus_full_drops_message(self):
        stt = FakeSTT()
        hub = Hub(stt=stt)  # type: ignore[arg-type]
        # Per-platform queue with maxsize=1 — will be full after one item
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=1)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        dispatched: list[tuple[InboundMessage, Response]] = []

        async def capture(msg, resp):
            dispatched.append((msg, resp))

        hub.dispatch_response = capture  # type: ignore[assignment]

        # Fill the per-platform queue (don't start bus — no feeder to drain)
        from tests.core.conftest import make_inbound_message

        filler = make_inbound_message()
        hub.inbound_bus.put(Platform.TELEGRAM, filler)

        # Now send audio — it will be transcribed but put() raises QueueFull
        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            # Give time for the audio to be processed and dropped
            await asyncio.sleep(0.3)
            # The filler is still in the per-platform queue
            assert hub.inbound_bus.qsize(Platform.TELEGRAM) == 1
            # No crash, loop continues — the echo reply was dispatched before
            # the QueueFull, so dispatched should have the echo
            echo_replies = [r for _, r in dispatched if "\U0001f3a4" in r.content]
            assert len(echo_replies) == 1
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
