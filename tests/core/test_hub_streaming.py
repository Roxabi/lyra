"""Tests for Hub streaming dispatch and run-loop streaming behaviour."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from lyra.core import Agent, AgentBase, Hub, Pool
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
)
from lyra.core.render_events import TextRenderEvent
from tests.core.conftest import make_inbound_message

# ---------------------------------------------------------------------------
# Hub dispatch_streaming
# ---------------------------------------------------------------------------


class TestDispatchStreaming:
    async def test_dispatches_streaming_to_adapter(self) -> None:
        hub = Hub()
        received: list[str] = []

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound=None,
            ) -> None:
                async for event in events:  # type: ignore[union-attr]
                    if isinstance(event, TextRenderEvent):
                        received.append(event.text)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield TextRenderEvent(text="Hello", is_final=False)
            yield TextRenderEvent(text=" world", is_final=True)

        await hub.dispatch_streaming(msg, gen())
        assert received == ["Hello", " world"]

    async def test_updates_last_processed_at_on_streaming_success(self) -> None:
        """dispatch_streaming sets _last_processed_at on successful stream."""
        hub = Hub()

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound=None,
            ) -> None:
                async for _ in events:  # type: ignore[union-attr]
                    pass

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield TextRenderEvent(text="hi", is_final=True)

        await hub.dispatch_streaming(msg, gen())
        assert hub._last_processed_at is not None

    async def test_fallback_to_send_when_no_send_streaming(self) -> None:
        hub = Hub()
        sent: list[object] = []

        class LegacyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append(outbound)

        # LegacyAdapter intentionally lacks send_streaming to test fallback
        hub.register_adapter(Platform.TELEGRAM, "main", LegacyAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield TextRenderEvent(text="Hello", is_final=False)
            yield TextRenderEvent(text=" world", is_final=True)

        await hub.dispatch_streaming(msg, gen())
        assert len(sent) == 1
        # dispatch_streaming fallback now sends OutboundMessage.from_text(text)
        assert isinstance(sent[0], OutboundMessage)
        assert "Hello world" in str(sent[0].content)

    async def test_voice_streaming_streams_text_and_triggers_tts(self) -> None:
        """Voice modality: text streams to user, then TTS fires as background task."""
        hub = Hub(tts=MagicMock())  # type: ignore[arg-type]
        hub._audio_pipeline.synthesize_and_dispatch_audio = AsyncMock()  # type: ignore[method-assign]
        streamed: list[TextRenderEvent] = []

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound: object = None,
            ) -> None:
                async for chunk in events:  # type: ignore[union-attr]
                    streamed.append(chunk)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen():
            yield TextRenderEvent(text="Hello", is_final=False)
            yield TextRenderEvent(text=" world", is_final=True)

        await hub.dispatch_streaming(msg, gen())

        assert streamed == [
            TextRenderEvent(text="Hello", is_final=False),
            TextRenderEvent(text=" world", is_final=True),
        ]

        if hub._memory_tasks:
            await asyncio.gather(*hub._memory_tasks)
        hub._audio_pipeline.synthesize_and_dispatch_audio.assert_awaited_once_with(
            msg, "Hello world", agent_tts=None, fallback_language=None
        )

    async def test_voice_streaming_empty_text_no_tts(self) -> None:
        """Voice modality with empty/whitespace text does not trigger TTS."""
        hub = Hub(tts=MagicMock())  # type: ignore[arg-type]
        hub._audio_pipeline.synthesize_and_dispatch_audio = AsyncMock()  # type: ignore[method-assign]

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound: object = None,
            ) -> None:
                async for _ in events:  # type: ignore[union-attr]
                    pass

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen():
            yield TextRenderEvent(text="  ", is_final=False)
            yield TextRenderEvent(text=" ", is_final=True)

        await hub.dispatch_streaming(msg, gen())
        hub._audio_pipeline.synthesize_and_dispatch_audio.assert_not_awaited()

    async def test_voice_streaming_no_tts_service_streams_normally(
        self,
    ) -> None:
        """When TTS is not configured, voice streaming works like normal."""
        hub = Hub()
        streamed: list[str] = []

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound: object = None,
            ) -> None:
                async for chunk in events:  # type: ignore[union-attr]
                    if isinstance(chunk, TextRenderEvent):
                        streamed.append(chunk.text)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen():
            yield TextRenderEvent(text="Hello", is_final=True)

        await hub.dispatch_streaming(msg, gen())
        assert streamed == ["Hello"]

    async def test_voice_streaming_legacy_adapter_fallback(self) -> None:
        """Voice + legacy adapter (no send_streaming): collects text, TTS."""
        hub = Hub(tts=MagicMock())  # type: ignore[arg-type]
        hub._audio_pipeline.synthesize_and_dispatch_audio = AsyncMock()  # type: ignore[method-assign]
        sent: list[OutboundMessage] = []

        class LegacyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append(outbound)

        hub.register_adapter(Platform.TELEGRAM, "main", LegacyAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen():
            yield TextRenderEvent(text="Hello", is_final=False)
            yield TextRenderEvent(text=" world", is_final=True)

        await hub.dispatch_streaming(msg, gen())

        assert len(sent) == 1
        assert "Hello world" in str(sent[0].content)

        if hub._memory_tasks:
            await asyncio.gather(*hub._memory_tasks)
        hub._audio_pipeline.synthesize_and_dispatch_audio.assert_awaited_once()

    async def test_voice_streaming_dispatcher_path_triggers_tts(
        self,
    ) -> None:
        """Voice + dispatcher path: text streams via dispatcher, TTS after."""
        from lyra.core.hub.outbound_dispatcher import OutboundDispatcher

        hub = Hub(tts=MagicMock())  # type: ignore[arg-type]
        hub._audio_pipeline.synthesize_and_dispatch_audio = AsyncMock()  # type: ignore[method-assign]
        streamed: list[str] = []

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound: object = None,
            ) -> None:
                async for chunk in events:  # type: ignore[union-attr]
                    if isinstance(chunk, TextRenderEvent):
                        streamed.append(chunk.text)

        adapter = StreamAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        dispatcher = OutboundDispatcher("telegram", adapter)  # type: ignore[arg-type]
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", dispatcher)
        await dispatcher.start()

        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen():
            yield TextRenderEvent(text="Hello", is_final=False)
            yield TextRenderEvent(text=" world", is_final=True)

        try:
            # dispatch_streaming returns immediately (non-blocking for voice
            # with dispatcher); TTS fires as a background task.
            await asyncio.wait_for(hub.dispatch_streaming(msg, gen()), timeout=5.0)
            # Wait for the dispatcher to consume the stream and TTS to fire.
            if hub._memory_tasks:
                await asyncio.wait_for(asyncio.gather(*hub._memory_tasks), timeout=5.0)
        finally:
            await dispatcher.stop()

        assert streamed == ["Hello", " world"]
        hub._audio_pipeline.synthesize_and_dispatch_audio.assert_awaited_once_with(
            msg, "Hello world", agent_tts=None, fallback_language=None
        )


# ---------------------------------------------------------------------------
# Hub run loop with streaming agent
# ---------------------------------------------------------------------------


class TestHubRunStreaming:
    async def test_streaming_agent_dispatches_via_streaming(self) -> None:
        """Hub.run() detects async generator and calls dispatch_streaming."""
        hub = Hub()
        received_chunks: list[str] = []

        class StreamingAgent(AgentBase):
            async def process(  # type: ignore[override]
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ):
                yield TextRenderEvent(text="chunk1", is_final=False)
                yield TextRenderEvent(text="chunk2", is_final=True)

        class CapturingStreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: object,
                outbound=None,
            ) -> None:
                async for chunk in events:  # type: ignore[union-attr]
                    if isinstance(chunk, TextRenderEvent):
                        received_chunks.append(chunk.text)

        config = Agent(name="streamer", system_prompt="", memory_namespace="lyra")
        hub.register_agent(StreamingAgent(config))
        hub.register_adapter(Platform.TELEGRAM, "main", CapturingStreamAdapter())  # type: ignore[arg-type]
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "streamer", "telegram:main:chat:42"
        )

        msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
        await hub.bus.put(msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        assert received_chunks == ["chunk1", "chunk2"]
