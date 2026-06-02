"""Tests for Hub streaming dispatch and run-loop streaming behaviour."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from factory.core import Agent, AgentBase, Hub, Pool
from factory.core.hub.hub_protocol import ChannelAdapter
from factory.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
from factory.core.messaging.render_events import (
    RenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
)
from factory.core.ports.tts import TtsProtocol
from tests.conftest import TIMEOUT_FAST, TIMEOUT_SLOW
from tests.core.conftest import MockAdapter, make_inbound_message, push_to_hub


def _mock_tts_on(hub: Hub) -> AsyncMock:
    """Replace hub._audio_pipeline.synthesize_and_dispatch_audio with AsyncMock."""
    mock = AsyncMock()
    object.__setattr__(hub._audio_pipeline, "synthesize_and_dispatch_audio", mock)
    return mock


# ---------------------------------------------------------------------------
# Hub dispatch_streaming
# ---------------------------------------------------------------------------


class TestDispatchStreaming:
    async def test_dispatches_streaming_to_adapter(self) -> None:
        hub = Hub()
        received: list[str] = []

        class StreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for event in events:
                    if isinstance(event, TextDeltaRenderEvent):
                        received.append(event.delta)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="Hello")
            yield TextDeltaRenderEvent(message_id="msg1", delta=" world")
            yield TextEndRenderEvent(message_id="msg1")

        await hub.dispatch_streaming(msg, gen())
        assert received == ["Hello", " world"]

    async def test_updates_last_processed_at_on_streaming_success(self) -> None:
        """dispatch_streaming sets _last_processed_at on successful stream."""
        hub = Hub()

        class StreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for _ in events:
                    pass

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="hi")
            yield TextEndRenderEvent(message_id="msg1")

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
        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast(ChannelAdapter, LegacyAdapter()),
        )
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="Hello")
            yield TextDeltaRenderEvent(message_id="msg1", delta=" world")
            yield TextEndRenderEvent(message_id="msg1")

        await hub.dispatch_streaming(msg, gen())
        assert len(sent) == 1
        # dispatch_streaming fallback now sends OutboundMessage.from_text(text)
        assert isinstance(sent[0], OutboundMessage)
        assert "Hello world" in str(sent[0].content)

    async def test_voice_streaming_streams_text_and_triggers_tts(self) -> None:
        """Voice modality: text streams to user, then TTS fires as background task."""
        hub = Hub(tts=cast(TtsProtocol, MagicMock()))
        _mock_synth = AsyncMock()
        object.__setattr__(
            hub._audio_pipeline,
            "synthesize_and_dispatch_audio",
            _mock_synth,
        )
        streamed: list[RenderEvent] = []

        class StreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for chunk in events:
                    streamed.append(chunk)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="Hello")
            yield TextDeltaRenderEvent(message_id="msg1", delta=" world")
            yield TextEndRenderEvent(message_id="msg1")

        await hub.dispatch_streaming(msg, gen())

        assert all(
            isinstance(e, TextDeltaRenderEvent | TextEndRenderEvent) for e in streamed
        )
        assert streamed == [
            TextDeltaRenderEvent(message_id="msg1", delta="Hello"),
            TextDeltaRenderEvent(message_id="msg1", delta=" world"),
            TextEndRenderEvent(message_id="msg1"),
        ]

        if hub._memory_tasks:
            await asyncio.gather(*hub._memory_tasks)
        _mock_synth.assert_awaited_once_with(
            msg, "Hello world", agent_tts=None, fallback_language=None
        )

    async def test_voice_streaming_empty_text_no_tts(self) -> None:
        """Voice modality with empty/whitespace text does not trigger TTS."""
        hub = Hub(tts=cast(TtsProtocol, MagicMock()))
        _mock_synth = AsyncMock()
        object.__setattr__(
            hub._audio_pipeline,
            "synthesize_and_dispatch_audio",
            _mock_synth,
        )

        class StreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for _ in events:
                    pass

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="  ")
            yield TextDeltaRenderEvent(message_id="msg1", delta=" ")
            yield TextEndRenderEvent(message_id="msg1")

        await hub.dispatch_streaming(msg, gen())
        _mock_synth.assert_not_awaited()

    async def test_voice_streaming_no_tts_service_streams_normally(
        self,
    ) -> None:
        """When TTS is not configured, voice streaming works like normal."""
        hub = Hub()
        streamed: list[str] = []

        class StreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for chunk in events:
                    if isinstance(chunk, TextDeltaRenderEvent):
                        streamed.append(chunk.delta)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="Hello")
            yield TextEndRenderEvent(message_id="msg1")

        await hub.dispatch_streaming(msg, gen())
        assert streamed == ["Hello"]

    async def test_voice_streaming_legacy_adapter_fallback(self) -> None:
        """Voice + legacy adapter (no send_streaming): collects text, TTS."""
        hub = Hub(tts=cast(TtsProtocol, MagicMock()))
        _mock_synth = AsyncMock()
        object.__setattr__(
            hub._audio_pipeline,
            "synthesize_and_dispatch_audio",
            _mock_synth,
        )
        sent: list[OutboundMessage] = []

        class LegacyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append(outbound)

        # LegacyAdapter intentionally lacks send_streaming — tests voice fallback path
        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            cast(ChannelAdapter, LegacyAdapter()),
        )
        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="Hello")
            yield TextDeltaRenderEvent(message_id="msg1", delta=" world")
            yield TextEndRenderEvent(message_id="msg1")

        await hub.dispatch_streaming(msg, gen())

        assert len(sent) == 1
        assert "Hello world" in str(sent[0].content)

        if hub._memory_tasks:
            await asyncio.gather(*hub._memory_tasks)
        _mock_synth.assert_awaited_once()

    async def test_voice_streaming_dispatcher_path_triggers_tts(
        self,
    ) -> None:
        """Voice + dispatcher path: text streams via dispatcher, TTS after."""
        from factory.core.hub.outbound.outbound_dispatcher import OutboundDispatcher

        hub = Hub(tts=cast(TtsProtocol, MagicMock()))
        _mock_synth = AsyncMock()
        object.__setattr__(
            hub._audio_pipeline,
            "synthesize_and_dispatch_audio",
            _mock_synth,
        )
        streamed: list[str] = []

        class StreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for chunk in events:
                    if isinstance(chunk, TextDeltaRenderEvent):
                        streamed.append(chunk.delta)

        adapter = StreamAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        dispatcher = OutboundDispatcher("telegram", adapter)
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", dispatcher)
        await dispatcher.start()

        msg = make_inbound_message(platform="telegram", bot_id="main", modality="voice")

        async def gen() -> AsyncIterator[RenderEvent]:
            yield TextDeltaRenderEvent(message_id="msg1", delta="Hello")
            yield TextDeltaRenderEvent(message_id="msg1", delta=" world")
            yield TextEndRenderEvent(message_id="msg1")

        try:
            # dispatch_streaming returns immediately (non-blocking for voice
            # with dispatcher); TTS fires as a background task.
            await asyncio.wait_for(
                hub.dispatch_streaming(msg, gen()), timeout=TIMEOUT_SLOW
            )
            # Wait for the dispatcher to consume the stream and TTS to fire.
            if hub._memory_tasks:
                await asyncio.wait_for(
                    asyncio.gather(*hub._memory_tasks), timeout=TIMEOUT_SLOW
                )
        finally:
            await dispatcher.stop()

        assert streamed == ["Hello", " world"]
        _mock_synth.assert_awaited_once_with(
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
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: Callable[[str], Awaitable[None]] | None = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                async def _stream() -> AsyncIterator[RenderEvent]:
                    yield TextDeltaRenderEvent(message_id="msg1", delta="chunk1")
                    yield TextDeltaRenderEvent(message_id="msg1", delta="chunk2")
                    yield TextEndRenderEvent(message_id="msg1")

                return _stream()

        class CapturingStreamAdapter(MockAdapter):
            async def send_streaming(
                self,
                original_msg: InboundMessage,
                events: AsyncIterator[RenderEvent],
                outbound: OutboundMessage | None = None,
            ) -> None:
                async for chunk in events:
                    if isinstance(chunk, TextDeltaRenderEvent):
                        received_chunks.append(chunk.delta)

        config = Agent(name="streamer", system_prompt="", memory_namespace="lyra")
        hub.register_agent(StreamingAgent(config))
        hub.register_adapter(Platform.TELEGRAM, "main", CapturingStreamAdapter())
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "streamer", "telegram:main:chat:42"
        )

        msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
        await push_to_hub(hub, msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=TIMEOUT_FAST)
        except asyncio.TimeoutError:
            pass

        assert received_chunks == ["chunk1", "chunk2"]
