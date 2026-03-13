"""Tests for MessagePipeline — stage isolation and integration (#208)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent import Agent, AgentBase
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import (
    Action,
    Hub,
    MessagePipeline,
)
from lyra.core.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
from lyra.core.pool import Pool
from tests.core.conftest import make_inbound_message

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


class _MockAdapter:
    """Minimal adapter that records sent messages."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage,
    ) -> None:
        self.sent.append(outbound)

    async def send_streaming(
        self, original_msg: InboundMessage, chunks: object,
        outbound: object = None,
    ) -> None:
        pass

    async def render_audio(
        self, msg: object, inbound: InboundMessage,
    ) -> None:
        pass

    async def render_attachment(
        self, msg: object, inbound: InboundMessage,
    ) -> None:
        pass


class _NullAgent(AgentBase):
    """Minimal agent for testing — returns a fixed response."""

    async def process(
        self, msg: InboundMessage, pool: Pool,
    ) -> Response:
        return Response(content="ok")


def _make_hub(**kwargs: object) -> Hub:
    """Build a Hub with an agent, adapter, and binding pre-wired."""
    hub = Hub(**kwargs)  # type: ignore[arg-type]

    agent = _NullAgent(Agent(
        name="lyra", system_prompt="", memory_namespace="lyra",
    ))
    hub.register_agent(agent)

    adapter = _MockAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
    hub.register_binding(
        Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*",
    )
    return hub


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
            Platform.TELEGRAM, "main", _MockAdapter(),  # type: ignore[arg-type]
        )
        pipeline = MessagePipeline(hub)
        msg = make_inbound_message()
        result = await pipeline.process(msg)
        assert result.action == Action.DROP

    async def test_no_agent_drops(self) -> None:
        hub = Hub()
        hub.register_adapter(
            Platform.TELEGRAM, "main", _MockAdapter(),  # type: ignore[arg-type]
        )
        # Binding references non-existent agent
        hub.register_binding(
            Platform.TELEGRAM, "main", "*",
            "nonexistent", "telegram:main:*",
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

    async def test_pairing_gate_drops_unpaired_user(
        self,
    ) -> None:
        """Pairing gate drops messages from unpaired users."""
        from lyra.core.pairing import PairingConfig, PairingManager

        config = PairingConfig(enabled=True)
        pm = PairingManager(
            config=config,
            db_path=":memory:",
            admin_user_ids={"admin"},
        )
        await pm.connect()
        try:
            hub = _make_hub(pairing_manager=pm)
            pipeline = MessagePipeline(hub)
            msg = make_inbound_message(user_id="unpaired-user")
            result = await pipeline.process(msg)
            assert result.action == Action.DROP
        finally:
            await pm.close()

    async def test_no_adapter_registered_drops(self) -> None:
        """Adapter miss in terminal stage produces DROP."""
        hub = Hub()
        agent = _NullAgent(Agent(
            name="lyra", system_prompt="",
            memory_namespace="lyra",
        ))
        hub.register_agent(agent)
        # Binding exists but no adapter for discord
        hub.register_binding(
            Platform.DISCORD, "main", "*",
            "lyra", "discord:main:*",
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
        agent.command_router = router  # type: ignore[attr-defined]

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
        agent.command_router = router  # type: ignore[attr-defined]

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
            binding.pool_id, binding.agent_name,
        )
        submitted: list[InboundMessage] = []
        pool.submit = lambda m: submitted.append(m)  # type: ignore[assignment]

        await hub.bus.put(msg)
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

        assert len(submitted) == 1
        assert submitted[0] is msg
