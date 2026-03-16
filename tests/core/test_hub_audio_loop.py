"""Tests for Hub._audio_loop() — InboundAudio → STT → InboundMessage pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from lyra.core.hub import Hub
from lyra.core.message import InboundAudio, InboundMessage, Platform, Response
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audio(
    audio_id: str = "audio-1",
    platform: str = "telegram",
    mime_type: str = "audio/ogg",
) -> InboundAudio:
    return InboundAudio(
        id=audio_id,
        platform=platform,
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        audio_bytes=b"\x00" * 100,
        mime_type=mime_type,
        duration_ms=3000,
        file_id="file-1",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        user_name="Alice",
        platform_meta={"chat_id": 42, "is_group": False},
    )


@dataclass
class FakeTranscription:
    text: str
    language: str = "en"
    duration_seconds: float = 2.5


class FakeSTT:
    """Minimal STTService stub."""

    def __init__(self, text: str = "Hello world") -> None:
        self._text = text
        self.calls: list[str] = []

    async def transcribe(self, path):
        self.calls.append(str(path))
        return FakeTranscription(text=self._text)


class FailingSTT:
    async def transcribe(self, path):
        raise RuntimeError("GPU on fire")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hub_with_stt():
    """Hub with a FakeSTT and registered telegram platform."""
    stt = FakeSTT()
    hub = Hub(stt=stt)  # type: ignore[arg-type]
    hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
    hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)
    return hub, stt


@pytest.fixture()
def hub_no_stt():
    """Hub without STT configured."""
    hub = Hub()
    hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
    hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)
    return hub


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAudioLoopTranscription:
    """Happy path: audio → STT → InboundMessage re-enqueued."""

    @pytest.mark.asyncio()
    async def test_transcribed_audio_enqueued_as_inbound_message(self, hub_with_stt):
        hub, stt = hub_with_stt
        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        # Stub dispatch_response so the echo reply doesn't raise KeyError
        hub.dispatch_response = lambda msg, resp: asyncio.sleep(0)  # type: ignore[assignment]

        await hub.inbound_bus.start()
        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            # Wait for the message to appear on the inbound bus staging queue
            msg: InboundMessage = await asyncio.wait_for(
                hub.inbound_bus._staging.get(), timeout=2.0
            )
            assert msg.id == "audio-1"
            assert msg.text == "Hello world"
            assert "\U0001f3a4" in msg.text_raw
            assert msg.platform == "telegram"
            assert msg.scope_id == "chat:42"
            assert msg.user_id == "alice"
            assert len(stt.calls) == 1
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
            await hub.inbound_bus.stop()


class TestAudioLoopNoSTT:
    """When STT is not configured, reply with stt_unsupported."""

    @pytest.mark.asyncio()
    async def test_no_stt_dispatches_unsupported_reply(self, hub_no_stt):
        hub = hub_no_stt
        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        dispatched: list[tuple[InboundMessage, Response]] = []
        done = asyncio.Event()

        async def capture_dispatch(msg, response):
            dispatched.append((msg, response))
            done.set()

        hub.dispatch_response = capture_dispatch  # type: ignore[assignment]

        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            await asyncio.wait_for(done.wait(), timeout=2.0)
            assert len(dispatched) == 1
            _, resp = dispatched[0]
            content = resp.content.lower()
            assert "not supported" in content or "not configured" in content
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()


class TestAudioLoopSTTFailure:
    """When STT raises, reply with stt_failed."""

    @pytest.mark.asyncio()
    async def test_stt_error_dispatches_failed_reply(self):
        stt = FailingSTT()
        hub = Hub(stt=stt)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        dispatched: list[tuple[InboundMessage, Response]] = []
        done = asyncio.Event()

        async def capture_dispatch(msg, response):
            dispatched.append((msg, response))
            done.set()

        hub.dispatch_response = capture_dispatch  # type: ignore[assignment]

        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            await asyncio.wait_for(done.wait(), timeout=2.0)
            assert len(dispatched) == 1
            _, resp = dispatched[0]
            assert "couldn't transcribe" in resp.content.lower()
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()


class TestAudioLoopNoise:
    """When STT returns noise tokens, reply with stt_noise."""

    @pytest.mark.parametrize("noise_text", ["[silence]", "", "  "])
    @pytest.mark.asyncio()
    async def test_noise_dispatches_noise_reply(self, noise_text: str):
        stt = FakeSTT(text=noise_text)
        hub = Hub(stt=stt)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        dispatched: list[tuple[InboundMessage, Response]] = []
        done = asyncio.Event()

        async def capture_dispatch(msg, response):
            dispatched.append((msg, response))
            done.set()

        hub.dispatch_response = capture_dispatch  # type: ignore[assignment]

        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            await asyncio.wait_for(done.wait(), timeout=2.0)
            assert len(dispatched) == 1
            _, resp = dispatched[0]
            assert "couldn't make out" in resp.content.lower()
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()


# ---------------------------------------------------------------------------
# S1 — T01/T03: InboundMessage.language field + _process_audio_item propagation
# ---------------------------------------------------------------------------


class TestInboundMessageLanguageField:
    """T01 — InboundMessage must carry an optional language field."""

    def test_inbound_message_has_language_field(self):

        from lyra.core.message import InboundMessage
        from lyra.core.trust import TrustLevel

        msg = InboundMessage(
            id="1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:1",
            user_id="tg:user:1",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            trust_level=TrustLevel.TRUSTED,
            language="fr",
        )
        assert msg.language == "fr"

    def test_inbound_message_language_defaults_none(self):
        from lyra.core.message import InboundMessage
        from lyra.core.trust import TrustLevel

        msg = InboundMessage(
            id="1",
            platform="telegram",
            bot_id="main",
            scope_id="chat:1",
            user_id="tg:user:1",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            trust_level=TrustLevel.TRUSTED,
        )
        assert msg.language is None


class TestProcessAudioItemPropagatesLanguage:
    """T03 — _process_audio_item propagates STT result.language to InboundMessage."""

    @pytest.mark.asyncio()
    async def test_process_audio_item_propagates_language(self):
        """STT returns language='fr' → enqueued InboundMessage.language == 'fr'."""
        stt = FakeSTT(text="bonjour")

        # Patch FakeSTT.transcribe to return language="fr"
        original_transcribe = stt.transcribe

        async def transcribe_with_lang(path):
            result = await original_transcribe(path)
            return FakeTranscription(text=result.text, language="fr")

        stt.transcribe = transcribe_with_lang

        hub = Hub(stt=stt)  # type: ignore[arg-type]
        hub.inbound_bus.register(Platform.TELEGRAM, maxsize=10)
        hub.inbound_audio_bus.register(Platform.TELEGRAM, maxsize=10)

        # Stub dispatch_response so the echo reply doesn't raise KeyError
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
            assert msg.language == "fr"
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
            await hub.inbound_bus.stop()


class TestAudioLoopTaskDone:
    """Verify task_done() is called on the audio bus after processing."""

    @pytest.mark.asyncio()
    async def test_task_done_called(self, hub_with_stt):
        hub, _ = hub_with_stt
        audio = _make_audio()
        hub.inbound_audio_bus.put(Platform.TELEGRAM, audio)

        # Stub dispatch_response so the echo reply doesn't raise KeyError
        hub.dispatch_response = lambda msg, resp: asyncio.sleep(0)  # type: ignore[assignment]

        await hub.inbound_bus.start()
        await hub.inbound_audio_bus.start()
        task = asyncio.create_task(hub._audio_pipeline.run())
        try:
            # Wait for re-enqueued message
            await asyncio.wait_for(hub.inbound_bus._staging.get(), timeout=2.0)
            # Give the loop time to call task_done
            await asyncio.sleep(0.05)
            # join() should return immediately since task_done was called
            await asyncio.wait_for(hub.inbound_audio_bus._staging.join(), timeout=1.0)
        finally:
            task.cancel()
            await hub.inbound_audio_bus.stop()
            await hub.inbound_bus.stop()
