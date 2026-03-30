"""Tests for AudioPipeline constraint branches.

Covers: trust-level exit, rate-limit branch, slash-command injection guard,
transcript length cap, inbound bus full.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from lyra.core.hub import Hub
from lyra.core.message import InboundMessage, Platform, Response
from lyra.core.trust import TrustLevel
from tests.core.conftest import FakeSTT, make_audio, make_inbound_message

if TYPE_CHECKING:
    from lyra.stt import STTService

# ---------------------------------------------------------------------------
# File-local helpers
# ---------------------------------------------------------------------------


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
        hub = Hub(stt=cast("STTService", stt))
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        capture = _DispatchCapture()
        object.__setattr__(hub, "dispatch_response", capture)

        # Blocked audio
        audio = make_audio(trust_level=TrustLevel.BLOCKED)
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        # Followed by a normal audio to prove the loop didn't stall
        normal = make_audio(audio_id="audio-2", trust_level=TrustLevel.TRUSTED)
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
# Rate Limit
# ---------------------------------------------------------------------------


class TestAudioPipelineRateLimit:
    """Audio exceeding per-user rate limit gets a rate_limited reply."""

    @pytest.mark.asyncio()
    async def test_rate_limited_audio_gets_reply(self):
        stt = FakeSTT()
        # rate_limit=1 means second audio from same user triggers limit
        hub = Hub(stt=cast("STTService", stt), rate_limit=1, rate_window=60)
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        capture = _DispatchCapture()
        object.__setattr__(hub, "dispatch_response", capture)

        # First audio consumes the rate allowance
        audio1 = make_audio(audio_id="audio-1")
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio1)

        # Second audio should be rate-limited
        audio2 = make_audio(audio_id="audio-2")
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
        hub = Hub(stt=cast("STTService", stt))
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        capture = _DispatchCapture()
        object.__setattr__(hub, "dispatch_response", capture)

        audio = make_audio()
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
        hub = Hub(stt=cast("STTService", stt))
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        object.__setattr__(hub, "dispatch_response", lambda msg, resp: asyncio.sleep(0))

        audio = make_audio()
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
        hub = Hub(stt=cast("STTService", stt))
        # Per-platform queue with maxsize=1 — will be full after one item
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=1)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        dispatched: list[tuple[InboundMessage, Response]] = []

        async def capture(msg, resp):
            dispatched.append((msg, resp))

        object.__setattr__(hub, "dispatch_response", capture)

        # Fill the per-platform queue (don't start bus — no feeder to drain)
        filler = make_inbound_message()
        hub.inbound_bus.put(Platform.TELEGRAM, filler)

        # Now send audio — it will be transcribed but put() raises QueueFull
        audio = make_audio()
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
