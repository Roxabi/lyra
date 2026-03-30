"""Tests for Hub circuit-breaker streaming: SC-08/09/10 and msg_manager injection."""  # noqa: E501

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from lyra.core import Agent, AgentBase, Hub, Pool, Response
from lyra.core.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from lyra.core.hub.hub_protocol import ChannelAdapter
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
)
from tests.core.conftest import make_circuit_registry, make_inbound_message

# ---------------------------------------------------------------------------
# SC-10 — Clean streaming records hub circuit success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hub_records_success_on_clean_streaming() -> None:
    """SC-10: Clean streaming → hub circuit success recorded (failure_count stays 0)."""
    # Arrange
    registry = make_circuit_registry()
    hub = Hub(circuit_registry=registry)

    class SilentAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            pass

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks,
            outbound=None,
        ) -> None:
            async for _ in chunks:
                pass

    class CleanStreamingAgent:
        name = "test"
        command_router = None

        def process(self, msg: InboundMessage, pool: Pool, *, on_intermediate=None):
            async def gen():
                yield "hello"

            return gen()

    hub.register_adapter(Platform.TELEGRAM, "main", cast("ChannelAdapter", SilentAdapter()))
    hub.register_agent(cast("AgentBase", CleanStreamingAgent()))
    hub.register_binding(Platform.TELEGRAM, "main", "*", "test", "telegram:main:*")

    # Act
    await hub.bus.put(make_inbound_message())
    hub_task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.1)
    hub_task.cancel()
    try:
        await hub_task
    except asyncio.CancelledError:
        pass

    # Assert — hub circuit breaker must not have accumulated any failures
    hub_status = registry["hub"].get_status()
    assert hub_status.failure_count == 0


# ---------------------------------------------------------------------------
# SC-09 — Mid-stream exception records anthropic circuit failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mid_stream_failure_records_anthropic_failure() -> None:
    """SC-09: Streaming exception → circuits['anthropic'].record_failure() called."""
    # Arrange
    registry = make_circuit_registry()
    hub = Hub(circuit_registry=registry)

    class SilentAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            pass

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks,
            outbound=None,
        ) -> None:
            async for _ in chunks:
                pass  # exception propagates from the generator

    class FailingStreamAgent:
        name = "test"
        command_router = None

        def process(self, msg: InboundMessage, pool: Pool, *, on_intermediate=None):
            from lyra.errors import ProviderError

            async def gen():
                yield "partial"
                raise ProviderError("API error mid-stream")

            return gen()

    hub.register_adapter(Platform.TELEGRAM, "main", cast("ChannelAdapter", SilentAdapter()))
    hub.register_agent(cast("AgentBase", FailingStreamAgent()))
    hub.register_binding(Platform.TELEGRAM, "main", "*", "test", "telegram:main:*")

    # Act
    await hub.bus.put(make_inbound_message())
    hub_task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.1)
    hub_task.cancel()
    try:
        await hub_task
    except asyncio.CancelledError:
        pass

    # Assert
    ant_status = registry["anthropic"].get_status()
    assert ant_status.failure_count >= 1, (
        f"Expected anthropic failure_count >= 1, got {ant_status.failure_count}"
    )


# SC-08 — Hub circuit opens after consecutive processing failures


@pytest.mark.asyncio
async def test_hub_circuit_opens_after_threshold() -> None:
    """SC-08: Hub circuit OPEN after failure_threshold consecutive failures."""
    # Arrange — hub CB with threshold=2 so test is fast
    hub_cb = CircuitBreaker("hub", failure_threshold=2, recovery_timeout=60)
    registry = make_circuit_registry(hub=hub_cb)
    hub = Hub(circuit_registry=registry)

    class SilentAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            pass

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks,
            outbound=None,
        ) -> None:
            async for _ in chunks:
                pass

    class AlwaysFailStreamAgent:
        name = "test"
        command_router = None

        def process(self, msg: InboundMessage, pool: Pool, *, on_intermediate=None):
            async def gen():
                yield "x"
                raise RuntimeError("forced failure")

            return gen()

    hub.register_adapter(Platform.TELEGRAM, "main", cast("ChannelAdapter", SilentAdapter()))
    hub.register_agent(cast("AgentBase", AlwaysFailStreamAgent()))
    hub.register_binding(Platform.TELEGRAM, "main", "*", "test", "telegram:main:*")

    # Act — enqueue 2 messages to trip the threshold
    for _ in range(2):
        await hub.bus.put(make_inbound_message())
    hub_task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.2)
    hub_task.cancel()
    try:
        await hub_task
    except asyncio.CancelledError:
        pass

    # Assert — hub circuit must be OPEN
    from lyra.core.circuit_breaker import CircuitState

    hub_status = registry["hub"].get_status()
    assert hub_status.state == CircuitState.OPEN, (
        f"Expected hub circuit OPEN after {hub_cb.failure_threshold} failures, "
        f"got {hub_status.state} (failure_count={hub_status.failure_count})"
    )


# ---------------------------------------------------------------------------
# msg_manager injection — Hub returns TOML "generic" string on agent failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hub_msg_manager_injection_generic_on_agent_failure() -> None:
    """Injecting a real MessageManager causes Hub to return the TOML 'generic'
    string (not the hardcoded fallback) when agent.process() raises."""
    from pathlib import Path

    from lyra.core.messages import MessageManager

    TOML_PATH = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "lyra"
        / "config"
        / "messages.toml"
    )

    # Arrange
    mm = MessageManager(TOML_PATH)
    hub = Hub(msg_manager=mm)

    sent_responses: list[OutboundMessage] = []

    class CapturingAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            sent_responses.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            from collections.abc import AsyncIterator as _AI

            async for _ in cast("_AI[object]", chunks):
                pass

    class FailingAgent(AgentBase):
        async def process(
            self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
        ) -> Response:
            raise RuntimeError("simulated agent failure")

    config = Agent(name="failing", system_prompt="", memory_namespace="test")
    hub.register_agent(FailingAgent(config))
    hub.register_adapter(Platform.TELEGRAM, "main", cast("ChannelAdapter", CapturingAdapter()))
    hub.register_binding(
        Platform.TELEGRAM, "main", "chat:42", "failing", "telegram:main:chat:42"
    )

    # Act — put one message; hub processes it and sends an error reply
    msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
    await hub.bus.put(msg)
    hub_task = asyncio.create_task(hub.run())
    await asyncio.sleep(0.1)
    hub_task.cancel()
    try:
        await hub_task
    except asyncio.CancelledError:
        pass

    # Assert — error reply content matches the TOML value, not a hardcoded string
    assert len(sent_responses) == 1
    expected = mm.get("generic")
    # dispatch_response converts Response → OutboundMessage; text is preserved
    assert expected in str(sent_responses[0].content)
