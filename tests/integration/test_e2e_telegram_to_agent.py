"""End-to-end: Telegram message → Hub → Pool → SimpleAgent → mocked
LLM → outbound reply.

No NATS, no real CLI pool — everything in-process with mocked LlmProvider.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from lyra.agents.simple_agent import SimpleAgent
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.auth.trust import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    TelegramMeta,
)
from lyra.core.messaging.render_events import RenderEvent
from lyra.llm.base import LlmResult
from tests.core.conftest import _MockAdapter, push_to_hub

if TYPE_CHECKING:
    from lyra.core.messaging.message import OutboundAudioChunk


class _RecordingAdapter(_MockAdapter):
    """Adapter that records both send() and send_streaming() calls."""

    def __init__(self) -> None:
        super().__init__()
        self.streamed: list[OutboundMessage] = []

    async def send(
        self,
        original_msg: InboundMessage,
        outbound: OutboundMessage,
    ) -> None:
        del original_msg
        self.sent.append(outbound)

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        events: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        del original_msg, events
        if outbound is not None:
            self.streamed.append(outbound)

    async def render_audio_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        del chunks, inbound


def _make_hub_with_agent(agent: SimpleAgent) -> Hub:
    """Build a Hub with the given agent, a Telegram adapter, and binding."""
    registry = CircuitRegistry()
    registry.register(CircuitBreaker("claude-cli"))
    hub = Hub(circuit_registry=registry)
    hub.register_agent(agent)
    adapter = _RecordingAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)
    hub.register_binding(
        Platform.TELEGRAM, "main", "*", agent.config.name, "telegram:main:*"
    )
    return hub


def _make_telegram_message(text: str = "hello") -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
    )


def _make_mock_provider(result_text: str) -> MagicMock:
    """Return a mocked LlmProvider that returns *result_text*."""
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=LlmResult(result=result_text, session_id="s1")
    )
    provider.is_alive = MagicMock(return_value=True)
    return provider


class TestE2ETelegramToAgent:
    async def test_message_reaches_adapter_with_llm_result(self) -> None:
        """Full flow: Telegram msg → Hub → Pool → SimpleAgent → LLM → adapter.send()."""
        # Arrange
        provider = _make_mock_provider("mocked-llm-reply")
        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=ModelConfig(),
        )
        agent = SimpleAgent(config, provider)
        hub = _make_hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        msg = _make_telegram_message("hi bot")

        # Act — push message, run hub until message is processed
        await push_to_hub(hub, msg)
        try:
            await asyncio.wait_for(hub.run(), timeout=1.0)
        except asyncio.TimeoutError:
            pass  # hub.run() loops forever

        # Assert — adapter received the outbound response
        assert len(adapter.sent) == 1
        outbound = adapter.sent[0]
        assert isinstance(outbound, OutboundMessage)
        assert outbound.to_text() == "mocked-llm-reply"

    async def test_agent_receives_correct_pool_id_and_text(self) -> None:
        """Verify the mocked provider was called with the right pool_id and text."""
        provider = _make_mock_provider("ok")
        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=ModelConfig(),
        )
        agent = SimpleAgent(config, provider)
        hub = _make_hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        msg = _make_telegram_message("test prompt")
        await push_to_hub(hub, msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Assert provider called with correct pool_id and text
        provider.complete.assert_awaited_once()
        args = provider.complete.call_args[0]
        assert args[0] == "telegram:main:chat:42"  # pool_id
        assert args[1] == "<user_message>test prompt</user_message>"

    async def test_streaming_response_reaches_adapter(self) -> None:
        """Streaming path: provider.stream() → adapter.send_streaming()."""
        async def _fake_stream():
            yield TextLlmEvent(text="chunk1")
            yield TextLlmEvent(text=" chunk2")
            yield ResultLlmEvent(is_error=False, duration_ms=100, cost_usd=None)

        provider = MagicMock()
        provider.stream = MagicMock(return_value=_fake_stream())
        provider.is_alive = MagicMock(return_value=True)

        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=ModelConfig(streaming=True),
        )
        agent = SimpleAgent(config, provider)
        hub = _make_hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        msg = _make_telegram_message("stream me")
        await push_to_hub(hub, msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=1.0)
        except asyncio.TimeoutError:
            pass

        # Assert streaming was dispatched via send_streaming
        assert len(adapter.streamed) == 1
        outbound = adapter.streamed[0]
        assert isinstance(outbound, OutboundMessage)
