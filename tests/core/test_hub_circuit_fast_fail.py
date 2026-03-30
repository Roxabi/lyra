"""Tests for Hub circuit-breaker fast-fail: SC-07 anthropic circuit OPEN."""  # noqa: E501

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, cast

import pytest

from lyra.core import Hub, Pool

if TYPE_CHECKING:
    from lyra.core.agent import AgentBase
    from lyra.core.hub.hub_protocol import ChannelAdapter
from lyra.core.circuit_breaker import CircuitBreaker
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
)
from tests.core.conftest import make_circuit_registry, make_inbound_message

# ---------------------------------------------------------------------------
# SC-07 — Anthropic circuit OPEN: fast-fail reply, agent.process() skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_circuit_open_sends_fast_fail_and_skips_agent() -> None:
    """SC-07: anthropic OPEN → fast-fail reply sent, agent.process() skipped."""
    # Arrange — open anthropic circuit
    open_cb = CircuitBreaker("anthropic", failure_threshold=1, recovery_timeout=60)
    open_cb.record_failure()  # trips to OPEN
    registry = make_circuit_registry(anthropic=open_cb)

    hub = Hub(circuit_registry=registry)

    sent_responses: list[OutboundMessage] = []

    class CapturingAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            sent_responses.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks,
            outbound=None,
        ) -> None:
            async for _ in chunks:
                pass

    process_called = False

    class MockStreamingAgent:
        name = "test"
        command_router = None

        def process(self, msg: InboundMessage, pool: Pool, *, on_intermediate=None):
            nonlocal process_called
            process_called = True

            async def gen():
                yield "should not reach"

            return gen()

    hub.register_adapter(Platform.TELEGRAM, "main", cast("ChannelAdapter", CapturingAdapter()))
    hub.register_agent(cast("AgentBase", MockStreamingAgent()))
    hub.register_binding(Platform.TELEGRAM, "main", "*", "test", "telegram:main:*")

    # Act — put one message on bus and let hub process it
    await hub.bus.put(make_inbound_message())
    hub_task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.05)
    hub_task.cancel()
    try:
        await hub_task
    except asyncio.CancelledError:
        pass

    # Assert
    assert process_called is False, (
        "Agent.process() must not be called when anthropic circuit is OPEN"
    )
    assert len(sent_responses) == 1
    content_str = str(sent_responses[0].content).lower()
    assert "unavailable" in content_str
    assert "try again" in content_str


# ---------------------------------------------------------------------------
# SC-07 — Fast-fail reply includes retry_after seconds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_circuit_open_includes_retry_after() -> None:
    """SC-07: Fast-fail reply body includes a numeric retry_after value (e.g. '60s')."""
    # Arrange
    open_cb = CircuitBreaker("anthropic", failure_threshold=1, recovery_timeout=60)
    open_cb.record_failure()
    registry = make_circuit_registry(anthropic=open_cb)
    hub = Hub(circuit_registry=registry)

    sent_responses: list[OutboundMessage] = []

    class CapturingAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            sent_responses.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks,
            outbound=None,
        ) -> None:
            async for _ in chunks:
                pass

    class MockStreamingAgent:
        name = "test"
        command_router = None

        def process(self, msg: InboundMessage, pool: Pool, *, on_intermediate=None):
            async def gen():
                yield "x"

            return gen()

    hub.register_adapter(Platform.TELEGRAM, "main", cast("ChannelAdapter", CapturingAdapter()))
    hub.register_agent(cast("AgentBase", MockStreamingAgent()))
    hub.register_binding(Platform.TELEGRAM, "main", "*", "test", "telegram:main:*")

    # Act
    await hub.bus.put(make_inbound_message())
    hub_task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.05)
    hub_task.cancel()
    try:
        await hub_task
    except asyncio.CancelledError:
        pass

    # Assert
    assert len(sent_responses) == 1
    content_str = str(sent_responses[0].content)
    assert re.search(r"\d+s", content_str), (
        f"Expected retry_after seconds in: {content_str!r}"
    )
