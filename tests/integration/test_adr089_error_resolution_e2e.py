"""ADR-089 E2E — Telegram → Hub → SimpleAgent → resolved user error text.

In-process smoke: mocked LlmProvider, real Hub routing, real MessageManager
and resolve_user_error() wiring. No NATS / no live CLI.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.agents.simple_agent import SimpleAgent
from factory.core.agent import Agent
from factory.core.agent.agent_config import ModelConfig
from factory.core.hub import Hub
from factory.core.lifecycle.circuit_breaker import CircuitBreaker, CircuitRegistry
from factory.core.messaging.events import ResultLlmEvent
from factory.core.messaging.message import Platform
from factory.core.messaging.messages import MessageManager
from factory.core.messaging.render_events import (
    RunErrorRenderEvent,
    TextDeltaRenderEvent,
)
from factory.llm.base import LlmResult
from roxabi_contracts.errors import WorkerError
from tests.core.conftest import push_to_hub
from tests.integration.test_e2e_telegram_to_agent import (
    _make_telegram_message,
    _RecordingAdapter,
)

_MESSAGES = (
    Path(__file__).resolve().parents[2] / "src" / "factory" / "data" / "messages.toml"
)


def _message_manager() -> MessageManager:
    return MessageManager(_MESSAGES, language="en")


class _DrainingStreamingAdapter(_RecordingAdapter):
    """Records outbound payloads and drains render-event streams."""

    def __init__(self) -> None:
        super().__init__()
        self.run_error_messages: list[str] = []
        self.streamed_deltas: list[str] = []

    async def send_streaming(self, original_msg, events, outbound=None) -> None:
        del original_msg
        async for event in events:
            if isinstance(event, RunErrorRenderEvent):
                self.run_error_messages.append(event.message)
            elif isinstance(event, TextDeltaRenderEvent):
                self.streamed_deltas.append(event.delta)
        if outbound is not None:
            self.streamed.append(outbound)


def _hub_with_agent(agent: SimpleAgent) -> Hub:
    mm = _message_manager()
    registry = CircuitRegistry()
    registry.register(CircuitBreaker("claude-cli"))
    hub = Hub(circuit_registry=registry, msg_manager=mm)
    hub.register_agent(agent)
    adapter = _DrainingStreamingAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)
    hub.register_binding(
        Platform.TELEGRAM, "main", "*", agent.config.name, "telegram:main:*"
    )
    return hub


async def _run_hub_until_processed(hub: Hub) -> None:
    await push_to_hub(hub, _make_telegram_message("hi"))
    try:
        await asyncio.wait_for(hub.run(), timeout=2.0)
    except asyncio.TimeoutError:
        pass


@pytest.mark.smoke
class TestAdr089BlockingErrorE2E:
    async def test_rate_limit_template_reaches_telegram_adapter(self) -> None:
        mm = _message_manager()
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(
                error="You've hit your weekly limit",
                worker_error=WorkerError(
                    code="llm.rate_limit",
                    message="You've hit your weekly limit",
                    retryable=True,
                ),
            )
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=False),
            ),
            provider,
            msg_manager=mm,
        )
        hub = _hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        assert adapter.sent[0].to_text() == mm.get("rate_limit")

    async def test_cli_parse_passthrough_reaches_telegram_adapter(self) -> None:
        curated = "You've hit your weekly limit · resets 6pm (UTC)"
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(
                error=curated,
                worker_error=WorkerError(
                    code="cli.parse",
                    message=curated,
                    retryable=False,
                ),
            )
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=False),
            ),
            provider,
            msg_manager=_message_manager(),
        )
        hub = _hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        assert "weekly limit" in adapter.sent[0].to_text()

    async def test_flat_error_without_worker_error_is_generic(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(error="internal scraper stack trace")
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=False),
            ),
            provider,
            msg_manager=_message_manager(),
        )
        hub = _hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        text = adapter.sent[0].to_text()
        assert "scraper" not in text
        assert "stack trace" not in text
        assert text == "Something went wrong. Please try again."


@pytest.mark.smoke
class TestAdr089StreamingSoftErrorE2E:
    async def test_rate_limit_soft_error_reaches_streaming_adapter(self) -> None:
        mm = _message_manager()
        expected = mm.get("rate_limit")

        async def _error_stream() -> AsyncIterator[ResultLlmEvent]:
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=10,
                error_text="You've hit your weekly limit",
                worker_error=WorkerError(
                    code="llm.rate_limit",
                    message="You've hit your weekly limit",
                    retryable=True,
                ),
            )

        provider = MagicMock()
        provider.stream = MagicMock(return_value=_error_stream())
        provider.is_alive = MagicMock(return_value=True)

        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=True),
            ),
            provider,
            msg_manager=mm,
        )
        hub = _hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _DrainingStreamingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.streamed) == 1
        assert adapter.run_error_messages == [expected]