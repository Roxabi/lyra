"""Tests for D3/D4: Message, Pool, Agent, Hub core structures.

RED phase — tests use the new Platform-based API (RoutingKey, TelegramContext,
DiscordContext, dispatch_response, get_or_create_pool, run()). All tests that
exercise the new API are expected to FAIL until the backend-dev GREEN phase.
"""

from __future__ import annotations

import asyncio
import logging
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
from lyra.core.message import (
    DiscordContext,
    Platform,
    TelegramContext,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_message(
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = "alice",
) -> Message:
    return Message(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        user_id=user_id,
        user_name="Alice",
        is_mention=False,
        is_from_bot=False,
        content="hello",
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=TelegramContext(chat_id=42),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockAdapter:
    """Minimal ChannelAdapter for testing."""

    async def send(self, original_msg: Message, response: Response) -> None:
        pass


# ---------------------------------------------------------------------------
# T2 — Platform context dataclasses
# ---------------------------------------------------------------------------


class TestPlatformContext:
    def test_telegram_context_defaults(self) -> None:
        ctx = TelegramContext(chat_id=123)
        assert ctx.chat_id == 123
        assert ctx.topic_id is None
        assert ctx.is_group is False

    def test_telegram_context_is_frozen(self) -> None:
        ctx = TelegramContext(chat_id=1)
        with pytest.raises((AttributeError, TypeError)):
            ctx.chat_id = 99  # type: ignore[misc]

    def test_discord_context_defaults(self) -> None:
        ctx = DiscordContext(guild_id=1, channel_id=2, message_id=3)
        assert ctx.thread_id is None
        assert ctx.channel_type == "text"

    def test_discord_context_is_frozen(self) -> None:
        ctx = DiscordContext(guild_id=1, channel_id=2, message_id=3)
        with pytest.raises((AttributeError, TypeError)):
            ctx.guild_id = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T1 — Message (updated to new API)
# ---------------------------------------------------------------------------


class TestMessage:
    def test_fields(self) -> None:
        msg = make_message()
        assert msg.id == "msg-1"
        assert msg.platform == Platform.TELEGRAM
        assert msg.bot_id == "main"
        assert msg.user_id == "alice"
        assert msg.type == MessageType.TEXT
        assert msg.metadata == {}

    def test_content_can_be_image_content(self) -> None:
        msg = Message(
            id="msg-1",
            platform=Platform.TELEGRAM,
            bot_id="main",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            is_from_bot=False,
            content=ImageContent(url="https://example.com/img.png"),
            type=MessageType.IMAGE,
            timestamp=datetime.now(timezone.utc),
            platform_context=TelegramContext(chat_id=42),
        )
        assert isinstance(msg.content, ImageContent)
        assert msg.content.url == "https://example.com/img.png"

    def test_trust_defaults_to_user(self) -> None:
        msg = make_message()
        assert msg.trust == "user"

    def test_trust_can_be_set_to_system(self) -> None:
        msg = Message(
            id="sys-1",
            platform=Platform.TELEGRAM,
            bot_id="main",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            is_from_bot=False,
            content="internal",
            type=MessageType.SYSTEM,
            timestamp=datetime.now(timezone.utc),
            platform_context=TelegramContext(chat_id=42),
            trust="system",
        )
        assert msg.trust == "system"


# ---------------------------------------------------------------------------
# Pool (unchanged — no dependency on new API)
# ---------------------------------------------------------------------------


class TestPool:
    def test_initial_state(self) -> None:
        pool = Pool(pool_id="telegram:main:alice", agent_name="lyra")
        assert pool.pool_id == "telegram:main:alice"
        assert pool.agent_name == "lyra"
        assert pool.history == []
        # Verify each pool gets its own list (not a shared mutable default)
        pool2 = Pool(pool_id="telegram:main:bob", agent_name="lyra")
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
# Agent (unchanged)
# ---------------------------------------------------------------------------


class TestAgent:
    def test_mutable_for_hot_reload(self) -> None:
        agent = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )
        agent.name = "other"
        assert agent.name == "other"

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
# T4 — Hub adapter registry (new (Platform, str) key)
# ---------------------------------------------------------------------------


class TestRegisterAdapter:
    def test_registers_by_platform_and_bot_id(self) -> None:
        hub = Hub()
        a = MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", a)
        assert hub.adapter_registry[(Platform.TELEGRAM, "main")] is a

    def test_two_bots_same_platform_no_collision(self) -> None:
        hub = Hub()
        a1, a2 = MockAdapter(), MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "bot1", a1)
        hub.register_adapter(Platform.TELEGRAM, "bot2", a2)
        assert hub.adapter_registry[(Platform.TELEGRAM, "bot1")] is a1
        assert hub.adapter_registry[(Platform.TELEGRAM, "bot2")] is a2

    def test_overwrite_adapter(self) -> None:
        hub = Hub()
        a1, a2 = MockAdapter(), MockAdapter()
        hub.register_adapter(Platform.TELEGRAM, "main", a1)
        hub.register_adapter(Platform.TELEGRAM, "main", a2)
        assert hub.adapter_registry[(Platform.TELEGRAM, "main")] is a2


# ---------------------------------------------------------------------------
# T3 — Hub routing key (RoutingKey replaces BindingKey)
# ---------------------------------------------------------------------------


class TestRoutingKey:
    def test_exact_binding(self) -> None:
        hub = Hub()
        hub.register_adapter(Platform.TELEGRAM, "main", MockAdapter())
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )
        msg = make_message(platform=Platform.TELEGRAM, user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        assert binding.pool_id == "telegram:main:alice"

    def test_wildcard_does_not_bleed_across_platforms(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.DISCORD, "main", "*", "lyra", "discord:main:*")
        msg = make_message(platform=Platform.TELEGRAM, user_id="unknown_user")
        assert hub.resolve_binding(msg) is None

    def test_wildcard_fallback_same_platform(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")
        msg = make_message(platform=Platform.TELEGRAM, user_id="unknown")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.agent_name == "lyra"
        # Per-user pool_id must be synthesised from the real user_id, not the wildcard
        assert binding.pool_id == f"telegram:main:{msg.user_id}"

    def test_no_binding_returns_none(self) -> None:
        hub = Hub()
        msg = make_message(platform=Platform.TELEGRAM, user_id="nobody")
        assert hub.resolve_binding(msg) is None

    def test_binding_isolated_per_bot_id(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.TELEGRAM, "bot1", "alice", "lyra", "telegram:bot1:alice"
        )
        msg = make_message(platform=Platform.TELEGRAM, bot_id="bot2", user_id="alice")
        assert hub.resolve_binding(msg) is None

    def test_binding_isolated_per_platform(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )
        msg = make_message(platform=Platform.DISCORD, bot_id="main", user_id="alice")
        assert hub.resolve_binding(msg) is None

    def test_overwrite_binding(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "alice", "lyra", "pool_v1")
        hub.register_binding(Platform.TELEGRAM, "main", "alice", "lyra", "pool_v2")
        msg = make_message(platform=Platform.TELEGRAM, user_id="alice")
        binding = hub.resolve_binding(msg)
        assert binding is not None
        assert binding.pool_id == "pool_v2"

    def test_register_binding_raises_if_pool_id_shared_across_users(self) -> None:
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "alice", "lyra", "shared_pool")
        with pytest.raises(ValueError, match="already bound"):
            hub.register_binding(
                Platform.TELEGRAM, "main", "bob", "lyra", "shared_pool"
            )


# ---------------------------------------------------------------------------
# T5 — dispatch_response
# ---------------------------------------------------------------------------


class TestDispatchResponse:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[Message, Response]] = []

        class CapturingAdapter:
            async def send(self, original_msg: Message, response: Response) -> None:
                sent.append((original_msg, response))

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())
        msg = make_message(platform=Platform.TELEGRAM, bot_id="main")
        response = Response(content="pong")
        await hub.dispatch_response(msg, response)
        assert len(sent) == 1
        assert sent[0][1].content == "pong"

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_message(platform=Platform.TELEGRAM, bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))


# ---------------------------------------------------------------------------
# T6 — Unmatched routing: log warning, message consumed, loop continues
# ---------------------------------------------------------------------------


class TestUnmatchedRouting:
    async def test_unmatched_key_logs_warning_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hub = Hub()
        msg = make_message(platform=Platform.TELEGRAM, user_id="nobody")
        await hub.bus.put(msg)
        with caplog.at_level(logging.WARNING, logger="lyra.core.hub"):
            # run() loops forever — use a short timeout to exercise one iteration
            try:
                await asyncio.wait_for(hub.run(), timeout=0.1)
            except asyncio.TimeoutError:
                pass  # expected — run() never returns on its own
        assert any("unmatched" in r.message.lower() for r in caplog.records)
        assert hub.bus.empty()  # message was consumed (dropped, not re-queued)


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
            Platform.TELEGRAM, "main", "alice", "ghost", "telegram:main:alice"
        )
        msg = make_message(platform=Platform.TELEGRAM, bot_id="main", user_id="alice")
        await hub.bus.put(msg)

        with caplog.at_level(logging.WARNING, logger="lyra.core.hub"):
            try:
                await asyncio.wait_for(hub.run(), timeout=0.2)
            except asyncio.TimeoutError:
                pass  # expected — run() never returns on its own

        assert any("no agent registered" in r.message.lower() for r in caplog.records)
        assert hub.bus.empty()


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
            async def process(self, msg: Message, pool: Pool) -> Response:
                raise AssertionError("process() must not be called without adapter")

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        hub.register_agent(DummyAgent(config))
        hub.register_binding(
            Platform.TELEGRAM, "main", "alice", "lyra", "telegram:main:alice"
        )
        # Deliberately do NOT register an adapter for (TELEGRAM, "main")
        msg = make_message(platform=Platform.TELEGRAM, bot_id="main", user_id="alice")
        await hub.bus.put(msg)

        with caplog.at_level(logging.ERROR, logger="lyra.core.hub"):
            try:
                await asyncio.wait_for(hub.run(), timeout=0.2)
            except asyncio.TimeoutError:
                pass  # expected — run() never returns on its own

        assert any("no adapter registered" in r.message.lower() for r in caplog.records)
        assert hub.bus.empty()


# ---------------------------------------------------------------------------
# TestRateTimestampsCleanup — F6
# ---------------------------------------------------------------------------


class TestRateTimestampsCleanup:
    """_is_rate_limited() deletes the deque entry when all timestamps expire."""

    def test_deque_deleted_when_all_timestamps_expire(self) -> None:
        """When all timestamps in the sliding window fall outside the window, the
        deque is evicted from _rate_timestamps before a fresh entry is created.
        After the second call with advanced time, the deque contains exactly one
        timestamp — the new one — confirming the stale entry was removed."""
        import unittest.mock

        from lyra.core.hub import RoutingKey

        hub = Hub(rate_window=1)
        msg = make_message(platform=Platform.TELEGRAM, user_id="alice")

        # First call — inserts a timestamp for alice's RoutingKey
        hub._is_rate_limited(msg)
        key = RoutingKey(msg.platform, msg.bot_id, msg.user_id)
        assert key in hub._rate_timestamps
        assert len(hub._rate_timestamps[key]) == 1

        # Advance monotonic clock past the 1-second window
        now_plus_2 = hub._rate_timestamps[key][0] + 2
        with unittest.mock.patch(
            "lyra.core.hub.time.monotonic", return_value=now_plus_2
        ):
            result = hub._is_rate_limited(msg)

        # Not rate-limited (only 1 message in the fresh window)
        assert result is False
        # The old timestamp was evicted; the deque now holds only the new call's
        # timestamp. This proves the cleanup path ran (deque was deleted and
        # re-created, not merely appended to).
        assert len(hub._rate_timestamps[key]) == 1
        assert hub._rate_timestamps[key][0] == now_plus_2


# ---------------------------------------------------------------------------
# TestGenericErrorReply — F7
# ---------------------------------------------------------------------------


def test_generic_error_reply_is_user_facing_string() -> None:
    from lyra.core.message import GENERIC_ERROR_REPLY

    assert GENERIC_ERROR_REPLY == "Something went wrong. Please try again."
