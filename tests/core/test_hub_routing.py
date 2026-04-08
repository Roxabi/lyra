"""Tests for Hub routing key resolution, pool creation, and unmatched routing behaviour."""  # noqa: E501

from __future__ import annotations

import asyncio
import logging

import pytest

from lyra.core import Agent, AgentBase, Hub, Pool, Response
from lyra.core.message import InboundMessage, Platform
from tests.core.conftest import MockAdapter, make_inbound_message, push_to_hub

# ---------------------------------------------------------------------------
# T3 — Hub routing key (RoutingKey replaces BindingKey)
# ---------------------------------------------------------------------------


class TestRoutingKey:
    def test_exact_binding(self) -> None:
        hub = Hub()
        hub.register_adapter(Platform.TELEGRAM, "main", MockAdapter())
        # scope_id="chat:42" must match make_inbound_message()'s default
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )
        msg = make_inbound_message(platform="telegram", user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        assert binding.pool_id == "telegram:main:chat:42"

    def test_wildcard_does_not_bleed_across_platforms(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.DISCORD, "main", "*", "lyra", "discord:main:*")
        msg = make_inbound_message(platform="telegram", user_id="unknown_user")
        assert hub.resolve_binding(msg) is None

    def test_wildcard_fallback_same_platform(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")
        msg = make_inbound_message(platform="telegram", user_id="unknown")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        # Per-scope pool_id must be synthesised from the message scope, not the wildcard
        assert binding.pool_id == f"telegram:main:{msg.scope_id}"

    def test_no_binding_returns_none(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", user_id="nobody")
        assert hub.resolve_binding(msg) is None

    def test_binding_isolated_per_bot_id(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.TELEGRAM, "bot1", "chat:42", "lyra", "telegram:bot1:chat:42"
        )
        msg = make_inbound_message(platform="telegram", bot_id="bot2", user_id="alice")
        assert hub.resolve_binding(msg) is None

    def test_binding_isolated_per_platform(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )
        msg = make_inbound_message(
            platform="discord",
            bot_id="main",
            user_id="alice",
            scope_id="channel:99",
            platform_meta={
                "guild_id": 1,
                "channel_id": 99,
                "message_id": 1,
                "thread_id": None,
                "channel_type": "text",
            },
        )
        assert hub.resolve_binding(msg) is None

    def test_overwrite_binding(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "chat:42", "lyra", "pool_v1")
        hub.register_binding(Platform.TELEGRAM, "main", "chat:42", "lyra", "pool_v2")
        msg = make_inbound_message(platform="telegram", user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.pool_id == "pool_v2"

    def test_register_binding_raises_if_pool_id_shared_across_scopes(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:100", "lyra", "shared_pool"
        )
        with pytest.raises(ValueError, match="already bound"):
            hub.register_binding(
                Platform.TELEGRAM, "main", "chat:200", "lyra", "shared_pool"
            )


# ---------------------------------------------------------------------------
# T6 — Unmatched routing: log warning, message consumed, loop continues
# ---------------------------------------------------------------------------


class TestUnmatchedRouting:
    async def test_unmatched_key_logs_warning_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", user_id="nobody")
        await push_to_hub(hub, msg)
        with caplog.at_level(logging.WARNING, logger="lyra.core.hub"):
            # run() loops forever — use a short timeout to exercise one iteration
            try:
                await asyncio.wait_for(hub.run(), timeout=0.1)
            except asyncio.TimeoutError:
                pass  # expected — run() never returns on its own
        assert any("unmatched" in r.message.lower() for r in caplog.records)
        # message was consumed (dropped, not re-queued)
        assert hub.inbound_bus.staging_qsize() == 0


# ---------------------------------------------------------------------------
# T7 — Pool creation via hub
# ---------------------------------------------------------------------------


class TestPoolCreation:
    def test_creates_pool_with_given_id(self) -> None:
        hub = Hub()
        pool = hub.get_or_create_pool("telegram:main:alice", "lyra")
        assert pool.pool_id == "telegram:main:alice"
        assert pool.agent_name == "lyra"

    def test_two_bots_same_user_separate_pools(self) -> None:
        hub = Hub()
        p1 = hub.get_or_create_pool("telegram:bot1:alice", "lyra")
        p2 = hub.get_or_create_pool("telegram:bot2:alice", "lyra")
        assert p1 is not p2

    def test_same_id_returns_same_pool(self) -> None:
        hub = Hub()
        p1 = hub.get_or_create_pool("telegram:main:alice", "lyra")
        p2 = hub.get_or_create_pool("telegram:main:alice", "lyra")
        assert p1 is p2


# ---------------------------------------------------------------------------
# TestAgentRegistryMiss
# ---------------------------------------------------------------------------


class TestAgentRegistryMiss:
    """Hub run loop drops message when agent is in bindings but not registered."""

    async def test_message_dropped_when_agent_not_registered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hub = Hub()
        # Register a binding for "ghost" but do NOT call hub.register_agent()
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "ghost", "telegram:main:chat:42"
        )
        msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
        await push_to_hub(hub, msg)

        with caplog.at_level(logging.WARNING, logger="lyra.core.hub"):
            try:
                await asyncio.wait_for(hub.run(), timeout=0.2)
            except asyncio.TimeoutError:
                pass  # expected — run() never returns on its own

        assert any("no agent registered" in r.message.lower() for r in caplog.records)
        assert hub.inbound_bus.staging_qsize() == 0


# ---------------------------------------------------------------------------
# TestMissingAdapterDrop
# ---------------------------------------------------------------------------


class TestMissingAdapterDrop:
    """Hub run loop drops message (before calling LLM) when adapter is missing."""

    async def test_message_dropped_when_adapter_not_registered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hub = Hub()

        class DummyAgent(AgentBase):
            async def process(
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ) -> Response:
                raise AssertionError("process() must not be called without adapter")

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        hub.register_agent(DummyAgent(config))
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "lyra", "telegram:main:chat:42"
        )
        # Deliberately do NOT register an adapter for (TELEGRAM, "main")
        msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
        await push_to_hub(hub, msg)

        with caplog.at_level(logging.ERROR, logger="lyra.core.hub"):
            try:
                await asyncio.wait_for(hub.run(), timeout=0.2)
            except asyncio.TimeoutError:
                pass  # expected — run() never returns on its own

        assert any("no adapter registered" in r.message.lower() for r in caplog.records)
        assert hub.inbound_bus.staging_qsize() == 0


# ---------------------------------------------------------------------------
# Generic error reply
# ---------------------------------------------------------------------------


def test_generic_error_reply_is_user_facing_string() -> None:
    from lyra.core.message import GENERIC_ERROR_REPLY

    assert GENERIC_ERROR_REPLY == "Something went wrong. Please try again."


# ---------------------------------------------------------------------------
# API contract assertions
# ---------------------------------------------------------------------------


def test_hub_has_no_pairing_gate_drop() -> None:
    """Hub._pairing_gate_drop must not exist — auth is resolved at adapter level."""
    from lyra.core.hub import Hub

    assert not hasattr(
        Hub, "_pairing_gate_drop"
    ), "Hub._pairing_gate_drop must be removed — auth is resolved at adapter level"
