"""Tests for D3: Message, Pool, Agent, Hub core structures."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from lyra.core import Agent, Hub, Message, MessageType, Pool, Response
from lyra.core.hub import Binding


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

    def test_content_can_be_dict(self) -> None:
        msg = make_message()
        msg.content = {"url": "https://example.com/img.png"}
        assert isinstance(msg.content, dict)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


class TestPool:
    def test_initial_state(self) -> None:
        pool = Pool(pool_id="telegram_alice", agent_name="lyra")
        assert pool.pool_id == "telegram_alice"
        assert pool.agent_name == "lyra"
        assert pool.history == []

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
        assert order.index("end-A") < order.index("start-B") or order.index("end-B") < order.index("start-A")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestAgent:
    def test_frozen(self) -> None:
        agent = Agent(name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra")
        with pytest.raises((AttributeError, TypeError)):
            agent.name = "other"  # type: ignore[misc]

    def test_default_permissions(self) -> None:
        agent = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        assert agent.permissions == ()

    async def test_process_raises_not_implemented(self) -> None:
        agent = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        pool = Pool(pool_id="p", agent_name="lyra")
        with pytest.raises(NotImplementedError):
            await agent.process(make_message(), pool)


# ---------------------------------------------------------------------------
# Hub — init
# ---------------------------------------------------------------------------


class TestHubInit:
    def test_bus_is_bounded_queue(self) -> None:
        hub = Hub()
        assert isinstance(hub.bus, asyncio.Queue)
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

        class MockAdapter:
            async def send(self, msg: Message, response: Response) -> None:
                pass

        adapter = MockAdapter()
        hub.register_adapter("telegram", adapter)
        assert hub.adapter_registry["telegram"] is adapter

    def test_overwrite_adapter(self) -> None:
        hub = Hub()

        class MockAdapter:
            async def send(self, msg: Message, response: Response) -> None:
                pass

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

    def test_wildcard_binding(self) -> None:
        hub = Hub()
        hub.register_binding("telegram", "*", "lyra", "telegram_default")
        msg = make_message(channel="telegram", user_id="unknown_user")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.pool_id == "telegram_default"

    def test_exact_takes_precedence_over_wildcard(self) -> None:
        hub = Hub()
        hub.register_binding("telegram", "*", "lyra", "telegram_default")
        hub.register_binding("telegram", "alice", "lyra", "telegram_alice")
        msg = make_message(channel="telegram", user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.pool_id == "telegram_alice"

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
