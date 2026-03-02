"""Tests for D3: Message, Pool, Agent, Hub core structures."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from lyra.core import (
    Agent,
    AgentBase,
    Hub,
    ImageContent,
    Message,
    MessageType,
    Pool,
    Response,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_message(channel: str = "telegram", user_id: str = "alice") -> Message:
    return Message(
        id="msg-1",
        channel=channel,
        user_id=user_id,
        content="hello",
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockAdapter:
    """Minimal ChannelAdapter for testing."""

    async def send(self, original_msg: Message, response: Response) -> None:
        pass


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class TestMessage:
    def test_fields(self) -> None:
        msg = make_message()
        assert msg.id == "msg-1"
        assert msg.channel == "telegram"
        assert msg.user_id == "alice"
        assert msg.type == MessageType.TEXT
        assert msg.metadata == {}

    def test_content_can_be_image_content(self) -> None:
        msg = Message(
            id="msg-1",
            channel="telegram",
            user_id="alice",
            content=ImageContent(url="https://example.com/img.png"),
            type=MessageType.IMAGE,
            timestamp=datetime.now(timezone.utc),
        )
        assert isinstance(msg.content, ImageContent)
        assert msg.content.url == "https://example.com/img.png"

    def test_trust_defaults_to_user(self) -> None:
        msg = make_message()
        assert msg.trust == "user"

    def test_trust_can_be_set_to_system(self) -> None:
        msg = Message(
            id="sys-1",
            channel="telegram",
            user_id="alice",
            content="internal",
            type=MessageType.SYSTEM,
            timestamp=datetime.now(timezone.utc),
            trust="system",
        )
        assert msg.trust == "system"


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


class TestPool:
    def test_initial_state(self) -> None:
        pool = Pool(pool_id="telegram_alice", agent_name="lyra")
        assert pool.pool_id == "telegram_alice"
        assert pool.agent_name == "lyra"
        assert pool.history == []
        # Verify each pool gets its own list (not a shared mutable default)
        pool2 = Pool(pool_id="telegram_bob", agent_name="lyra")
        assert pool.history is not pool2.history

    def test_has_asyncio_lock(self) -> None:
        pool = Pool(pool_id="p1", agent_name="lyra")
        assert isinstance(pool.lock, asyncio.Lock)

    async def test_lock_is_per_pool(self) -> None:
        p1 = Pool(pool_id="p1", agent_name="lyra")
        p2 = Pool(pool_id="p2", agent_name="lyra")
        assert p1.lock is not p2.lock

    async def test_lock_serialises_access(self) -> None:
        pool = Pool(pool_id="p1", agent_name="lyra")
        order: list[str] = []

        async def task(label: str) -> None:
            async with pool.lock:
                order.append(f"start-{label}")
                await asyncio.sleep(0)
                order.append(f"end-{label}")

        await asyncio.gather(task("A"), task("B"))
        # The two tasks must NOT interleave
        assert order == ["start-A", "end-A", "start-B", "end-B"] or order == [
            "start-B",
            "end-B",
            "start-A",
            "end-A",
        ]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestAgent:
    def test_frozen(self) -> None:
        agent = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )
        with pytest.raises(AttributeError):
            agent.name = "other"  # type: ignore[misc]

    def test_default_permissions(self) -> None:
        agent = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        assert agent.permissions == ()

    def test_agent_base_cannot_be_instantiated_directly(self) -> None:
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        with pytest.raises(TypeError):
            AgentBase(config)  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Hub — init
# ---------------------------------------------------------------------------


class TestHubInit:
    def test_bus_is_bounded_queue(self) -> None:
        hub = Hub()
        assert isinstance(hub.bus, asyncio.Queue)
        assert Hub.BUS_SIZE == 100
        assert hub.bus.maxsize == Hub.BUS_SIZE

    def test_empty_registries(self) -> None:
        hub = Hub()
        assert hub.adapter_registry == {}
        assert hub.bindings == {}
        assert hub.pools == {}


# ---------------------------------------------------------------------------
# Hub — register_adapter
# ---------------------------------------------------------------------------


class TestRegisterAdapter:
    def test_registers_adapter(self) -> None:
        hub = Hub()
        adapter = MockAdapter()
        hub.register_adapter("telegram", adapter)
        assert hub.adapter_registry["telegram"] is adapter

    def test_overwrite_adapter(self) -> None:
        hub = Hub()
        a1, a2 = MockAdapter(), MockAdapter()
        hub.register_adapter("telegram", a1)
        hub.register_adapter("telegram", a2)
        assert hub.adapter_registry["telegram"] is a2


# ---------------------------------------------------------------------------
# Hub — register_binding / resolve_binding
# ---------------------------------------------------------------------------


class TestBindings:
    def test_exact_binding(self) -> None:
        hub = Hub()
        hub.register_binding("telegram", "alice", "lyra", "telegram_alice")
        msg = make_message(channel="telegram", user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        assert binding.pool_id == "telegram_alice"

    def test_wildcard_does_not_bleed_across_channels(self) -> None:
        hub = Hub()
        hub.register_binding("discord", "*", "lyra", "discord_default")
        msg = make_message(channel="telegram", user_id="unknown_user")
        assert hub.resolve_binding(msg) is None

    def test_no_binding_returns_none(self) -> None:
        hub = Hub()
        msg = make_message(channel="telegram", user_id="nobody")
        assert hub.resolve_binding(msg) is None

    def test_binding_isolated_per_channel(self) -> None:
        hub = Hub()
        hub.register_binding("telegram", "alice", "lyra", "tg_alice")
        msg = make_message(channel="discord", user_id="alice")
        assert hub.resolve_binding(msg) is None

    def test_overwrite_binding(self) -> None:
        hub = Hub()
        hub.register_binding("telegram", "alice", "lyra", "pool_v1")
        hub.register_binding("telegram", "alice", "lyra", "pool_v2")
        msg = make_message(channel="telegram", user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.pool_id == "pool_v2"

    def test_register_binding_raises_if_pool_id_shared_across_users(self) -> None:
        hub = Hub()
        hub.register_binding("telegram", "alice", "lyra", "shared_pool")
        with pytest.raises(ValueError, match="already bound"):
            hub.register_binding("telegram", "bob", "lyra", "shared_pool")
