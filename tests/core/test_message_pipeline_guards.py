"""Tests for MessagePipeline guard stages, terminal stages, integration, and
gate removal assertions (#208, #245)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent import Agent
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub
from lyra.core.hub.message_pipeline import Action, MessagePipeline
from lyra.core.message import InboundMessage, Platform, Response
from lyra.core.pool import Pool
from tests.core.conftest import (
    _make_hub,
    _MockAdapter,
    _NullAgent,
    make_inbound_message,
)

# -------------------------------------------------------------------
# Stage isolation tests (T7)
# -------------------------------------------------------------------


class TestPipelineGuardStages:
    """Each guard stage returns DROP or None independently."""

    async def test_unknown_platform_drops(self) -> None:
        hub = _make_hub()
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message(platform="unknown_plat")
        result = await pipeline.process(msg)
        assert result.action == Action.DROP

    async def test_rate_limited_drops(self) -> None:
        hub = _make_hub(rate_limit=1, rate_window=60)
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        # First message passes
        r1 = await pipeline.process(msg)
        assert r1.action == Action.SUBMIT_TO_POOL
        # Second message is rate limited
        r2 = await pipeline.process(msg)
        assert r2.action == Action.DROP

    async def test_no_binding_drops(self) -> None:
        hub = Hub()
        # Register adapter but no binding
        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            _MockAdapter(),
        )
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        result = await pipeline.process(msg)
        assert result.action == Action.DROP

    async def test_no_agent_drops(self) -> None:
        hub = Hub()
        hub.register_adapter(
            Platform.TELEGRAM,
            "main",
            _MockAdapter(),
        )
        # Binding references non-existent agent
        hub.register_binding(
            Platform.TELEGRAM,
            "main",
            "*",
            "nonexistent",
            "telegram:main:*",
        )
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        result = await pipeline.process(msg)
        assert result.action == Action.DROP

    async def test_circuit_breaker_open_drops(self) -> None:
        """Open circuit breaker in terminal stage produces DROP."""
        registry = CircuitRegistry()
        cb = CircuitBreaker(
            name="anthropic",
            failure_threshold=1,
            recovery_timeout=60,
        )
        registry.register(cb)
        # Trip the circuit breaker
        cb.record_failure()
        hub = _make_hub(circuit_registry=registry)
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        result = await pipeline.process(msg)
        assert result.action == Action.DROP

    async def test_no_adapter_registered_drops(self) -> None:
        """Adapter miss in terminal stage produces DROP."""
        hub = Hub()
        agent = _NullAgent(
            Agent(
                name="lyra",
                system_prompt="",
                memory_namespace="lyra",
            )
        )
        hub.register_agent(agent)
        # Binding exists but no adapter for discord
        hub.register_binding(
            Platform.DISCORD,
            "main",
            "*",
            "lyra",
            "discord:main:*",
        )
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message(
            platform="discord",
            scope_id="channel:1",
            platform_meta={
                "guild_id": 1,
                "channel_id": 1,
                "message_id": 1,
                "thread_id": None,
                "channel_type": "text",
            },
        )
        result = await pipeline.process(msg)
        assert result.action == Action.DROP


# -------------------------------------------------------------------
# Terminal stage tests (T7 continued)
# -------------------------------------------------------------------


class TestPipelineTerminalStages:
    """Command dispatch and pool submit."""

    async def test_command_dispatch_returns_command_handled(
        self,
    ) -> None:
        hub = _make_hub()
        pipeline = MessagePipeline(hub)

        # Give the agent a command router
        agent = hub.agent_registry["lyra"]
        router = MagicMock()
        router.is_command.return_value = True
        router.get_command_name.return_value = "/test"
        router.dispatch = AsyncMock(
            return_value=Response(content="cmd result"),
        )
        object.__setattr__(agent, "command_router", router)

        msg = make_inbound_message()
        result = await pipeline.process(msg)

        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None
        assert result.response.content == "cmd result"

    async def test_pool_submit_returns_submit_to_pool(
        self,
    ) -> None:
        hub = _make_hub()
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        result = await pipeline.process(msg)
        assert result.action == Action.SUBMIT_TO_POOL
        assert result.pool is not None


# -------------------------------------------------------------------
# Integration / happy-path tests (T8)
# -------------------------------------------------------------------


class TestPipelineIntegration:
    """End-to-end pipeline behavior matches pre-refactor Hub.run()."""

    async def test_valid_message_flows_to_pool(self) -> None:
        """A valid non-command message flows through all guard stages
        and reaches SUBMIT_TO_POOL."""
        hub = _make_hub()
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        result = await pipeline.process(msg)
        assert result.action == Action.SUBMIT_TO_POOL
        assert result.pool is not None
        assert isinstance(result.pool, Pool)

    async def test_command_dispatch_error_returns_generic(
        self,
    ) -> None:
        """When command dispatch raises, pipeline returns
        COMMAND_HANDLED with a generic error response."""
        hub = _make_hub()
        pipeline = MessagePipeline(hub)

        agent = hub.agent_registry["lyra"]
        router = MagicMock()
        router.is_command.return_value = True
        router.get_command_name.return_value = "/broken"
        router.dispatch = AsyncMock(
            side_effect=RuntimeError("boom"),
        )
        object.__setattr__(agent, "command_router", router)

        msg = make_inbound_message()
        result = await pipeline.process(msg)

        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None
        # Should be the generic error reply, not the exception
        assert "boom" not in (result.response.content or "")

    async def test_hub_run_delegates_to_pipeline(self) -> None:
        """Hub.run() processes a message via the pipeline and calls
        pool.submit for SUBMIT_TO_POOL."""
        hub = _make_hub()
        msg = make_inbound_message()

        # Pre-create the pool so we can spy on submit
        binding = hub.resolve_binding(msg)
        assert binding is not None
        pool = hub.get_or_create_pool(
            binding.pool_id,
            binding.agent_name,
        )
        submitted: list[InboundMessage] = []
        object.__setattr__(pool, "submit", lambda m: submitted.append(m))

        await hub.bus.put(msg)
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        assert len(submitted) == 1
        assert submitted[0] is msg


# ---------------------------------------------------------------------------
# SC8 — Hub/Pipeline gate removal assertions (#245)
# ---------------------------------------------------------------------------


class TestGateMethodsRemoved:
    """SC8: _pairing_gate_drop and _pairing_gate must not exist after #245."""

    def test_hub_pairing_gate_drop_removed(self) -> None:
        """Hub._pairing_gate_drop() must not exist (removed in #245, S4)."""
        assert not hasattr(Hub, "_pairing_gate_drop"), (
            "Hub._pairing_gate_drop must be removed — auth is resolved at adapter level"
        )

    def test_pipeline_pairing_gate_removed(self) -> None:
        """MessagePipeline._pairing_gate() must not exist (removed in #245, S4)."""
        assert not hasattr(MessagePipeline, "_pairing_gate"), (
            "MessagePipeline._pairing_gate must be removed"
            " — auth is resolved at adapter level"
        )
