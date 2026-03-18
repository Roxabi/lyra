"""Tests for Hub streaming dispatch and run-loop streaming behaviour."""

from __future__ import annotations

import asyncio

from lyra.core import Agent, AgentBase, Hub, Pool
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
)
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
                chunks: object,
                outbound=None,
            ) -> None:
                async for chunk in chunks:  # type: ignore[union-attr]
                    received.append(chunk)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield "Hello"
            yield " world"

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
                chunks: object,
                outbound=None,
            ) -> None:
                async for _ in chunks:  # type: ignore[union-attr]
                    pass

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield "hi"

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
            yield "Hello"
            yield " world"

        await hub.dispatch_streaming(msg, gen())
        assert len(sent) == 1
        # dispatch_streaming fallback now sends OutboundMessage.from_text(text)
        assert isinstance(sent[0], OutboundMessage)
        assert "Hello world" in str(sent[0].content)


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
                yield "chunk1"
                yield "chunk2"

        class CapturingStreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                async for chunk in chunks:  # type: ignore[union-attr]
                    received_chunks.append(chunk)

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
