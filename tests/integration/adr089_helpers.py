"""Shared helpers for ADR-089 Hub/OMP error-resolution E2E tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from factory.agents.simple_agent import SimpleAgent
from factory.core.hub import Hub
from factory.core.lifecycle.circuit_breaker import CircuitBreaker, CircuitRegistry
from factory.core.messaging.message import Platform
from factory.core.messaging.messages import MessageManager
from factory.core.messaging.render_events import (
    RunErrorRenderEvent,
    TextDeltaRenderEvent,
)
from tests.core.conftest import push_to_hub
from tests.integration.test_e2e_telegram_to_agent import (
    _make_telegram_message,
    _RecordingAdapter,
)

_MESSAGES = (
    Path(__file__).resolve().parents[2] / "src" / "factory" / "data" / "messages.toml"
)


def message_manager() -> MessageManager:
    return MessageManager(_MESSAGES, language="en")


class DrainingStreamingAdapter(_RecordingAdapter):
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


def hub_with_agent(agent: SimpleAgent) -> Hub:
    mm = message_manager()
    registry = CircuitRegistry()
    registry.register(CircuitBreaker("claude-cli"))
    hub = Hub(circuit_registry=registry, msg_manager=mm)
    hub.register_agent(agent)
    adapter = DrainingStreamingAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)
    hub.register_binding(
        Platform.TELEGRAM, "main", "*", agent.config.name, "telegram:main:*"
    )
    return hub


async def run_hub_until_processed(hub: Hub) -> None:
    await push_to_hub(hub, _make_telegram_message("hi"))
    try:
        await asyncio.wait_for(hub.run(), timeout=2.0)
    except asyncio.TimeoutError:
        pass