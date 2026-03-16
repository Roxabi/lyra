"""Tests for D3/D4: Message, Pool, Agent, Hub core structures.

RED phase — tests use the new Platform-based API (RoutingKey, TelegramContext,
DiscordContext, dispatch_response, get_or_create_pool, run()). All tests that
exercise the new API are expected to FAIL until the backend-dev GREEN phase.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core import (
    Agent,
    AgentBase,
    Hub,
    Pool,
    Response,
)
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from lyra.core.messages import MessageManager
from lyra.core.trust import TrustLevel

TOML_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "lyra"
    / "config"
    / "messages.toml"
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


from tests.core.conftest import (  # noqa: E402 (shared helper)
    make_inbound_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockAdapter:
    """Minimal ChannelAdapter for testing."""

    def normalize(self, raw: object) -> InboundMessage:
        raise NotImplementedError

    def normalize_audio(
        self,
        raw: object,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,  # noqa: E501
    ) -> InboundAudio:
        raise NotImplementedError

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        pass

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        chunks: object,
        outbound: object = None,
    ) -> None:
        pass

    async def render_audio(self, msg: object, inbound: InboundMessage) -> None:
        pass

    async def render_audio_stream(
        self, chunks: object, inbound: InboundMessage
    ) -> None:
        pass

    async def render_voice_stream(
        self, chunks: object, inbound: InboundMessage
    ) -> None:
        pass

    async def render_attachment(self, msg: object, inbound: InboundMessage) -> None:
        pass


# ---------------------------------------------------------------------------
# Pool (unchanged — no dependency on new API)
# ---------------------------------------------------------------------------


class TestPool:
    def _make_hub(self) -> MagicMock:
        hub = MagicMock()
        hub.agent_registry = {}
        hub.circuit_registry = None
        hub._msg_manager = None
        return hub

    def test_initial_state(self) -> None:
        hub = self._make_hub()
        pool = Pool(pool_id="telegram:main:alice", agent_name="lyra", ctx=hub)
        assert pool.pool_id == "telegram:main:alice"
        assert pool.agent_name == "lyra"
        assert pool.history == []
        # Verify each pool gets its own list (not a shared mutable default)
        pool2 = Pool(pool_id="telegram:main:bob", agent_name="lyra", ctx=hub)
        assert pool.history is not pool2.history

    def test_has_inbox_queue(self) -> None:
        hub = self._make_hub()
        pool = Pool(pool_id="p1", agent_name="lyra", ctx=hub)
        assert isinstance(pool._inbox, asyncio.Queue)

    def test_inbox_is_per_pool(self) -> None:
        hub = self._make_hub()
        p1 = Pool(pool_id="p1", agent_name="lyra", ctx=hub)
        p2 = Pool(pool_id="p2", agent_name="lyra", ctx=hub)
        assert p1._inbox is not p2._inbox

    def test_current_task_starts_none(self) -> None:
        hub = self._make_hub()
        pool = Pool(pool_id="p1", agent_name="lyra", ctx=hub)
        assert pool._current_task is None


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
    def test_inbound_bus_exists(self) -> None:
        from lyra.core.inbound_bus import InboundBus

        hub = Hub()
        assert Hub.BUS_SIZE == 100
        assert isinstance(hub.inbound_bus, InboundBus)
        # hub.bus is a backward-compat alias for the staging queue (unbounded)
        assert isinstance(hub.bus, asyncio.Queue)

    def test_per_platform_queue_is_bounded_after_register_adapter(self) -> None:
        hub = Hub()
        hub.register_adapter(Platform.TELEGRAM, "main", MockAdapter())
        assert hub.inbound_bus.qsize(Platform.TELEGRAM) == 0
        # Per-platform queue has BUS_SIZE maxsize
        assert hub.inbound_bus._queues[Platform.TELEGRAM].maxsize == Hub.BUS_SIZE

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
# T5 — dispatch_response
# ---------------------------------------------------------------------------


class TestDispatchResponse:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[InboundMessage, OutboundMessage]] = []

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append((original_msg, outbound))

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")
        response = Response(content="pong")
        await hub.dispatch_response(msg, response)
        assert len(sent) == 1
        # dispatch_response converts Response → OutboundMessage; text is preserved
        assert "pong" in str(sent[0][1].content)

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))

    async def test_updates_last_processed_at_on_success(self) -> None:
        """dispatch_response sets _last_processed_at on successful send."""
        hub = Hub()

        class DummyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", DummyAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        await hub.dispatch_response(msg, Response(content="ok"))
        assert hub._last_processed_at is not None

    async def test_no_update_last_processed_at_on_missing_adapter(self) -> None:
        """dispatch_response does NOT update _last_processed_at on KeyError."""
        hub = Hub()
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        with pytest.raises(KeyError):
            await hub.dispatch_response(msg, Response(content="x"))
        assert hub._last_processed_at is None


# ---------------------------------------------------------------------------
# #217 — dispatch_attachment
# ---------------------------------------------------------------------------


class TestDispatchAttachment:
    async def test_dispatches_to_correct_adapter(self) -> None:
        hub = Hub()
        sent: list[tuple[OutboundAttachment, InboundMessage]] = []

        class CapturingAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object, outbound=None
            ) -> None:
                pass

            async def render_attachment(
                self, attachment: OutboundAttachment, inbound: InboundMessage
            ) -> None:
                sent.append((attachment, inbound))

        hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        await hub.dispatch_attachment(msg, attachment)
        assert len(sent) == 1
        assert sent[0][0] is attachment

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        with pytest.raises(KeyError):
            await hub.dispatch_attachment(msg, attachment)

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()

        class DummyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self, original_msg: InboundMessage, chunks: object, outbound=None
            ) -> None:
                pass

            async def render_attachment(
                self, attachment: OutboundAttachment, inbound: InboundMessage
            ) -> None:
                pass

        hub.register_adapter(Platform.TELEGRAM, "main", DummyAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        attachment = OutboundAttachment(
            data=b"img", type="image", mime_type="image/png"
        )
        await hub.dispatch_attachment(msg, attachment)
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# #182 — dispatch_audio
# ---------------------------------------------------------------------------


class TestDispatchAudio:
    async def test_routes_to_dispatcher(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        dispatcher = MagicMock()
        dispatcher.enqueue_audio = MagicMock()
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", dispatcher)
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        await hub.dispatch_audio(msg, audio)
        dispatcher.enqueue_audio.assert_called_once_with(msg, audio)
        assert hub._last_processed_at is not None

    async def test_fallback_to_adapter(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        await hub.dispatch_audio(msg, audio)
        adapter.render_audio.assert_awaited_once_with(audio, msg)

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        with pytest.raises(KeyError):
            await hub.dispatch_audio(msg, audio)

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")
        audio = OutboundAudio(audio_bytes=b"ogg", mime_type="audio/ogg")
        await hub.dispatch_audio(msg, audio)
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# #182 — dispatch_audio_stream
# ---------------------------------------------------------------------------


class TestDispatchAudioStream:
    async def test_routes_to_dispatcher(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        dispatcher = MagicMock()
        dispatcher.enqueue_audio_stream = MagicMock()
        hub.register_outbound_dispatcher(Platform.TELEGRAM, "main", dispatcher)
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_audio_stream(msg, c)
        dispatcher.enqueue_audio_stream.assert_called_once_with(msg, c)
        assert hub._last_processed_at is not None

    async def test_fallback_to_adapter(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_audio_stream(msg, c)
        adapter.render_audio_stream.assert_awaited_once()
        call_args = adapter.render_audio_stream.call_args[0]
        assert call_args[0] is c
        assert call_args[1] is msg

    async def test_missing_adapter_raises(self) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", bot_id="ghost")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        with pytest.raises(KeyError):
            await hub.dispatch_audio_stream(msg, chunks())

    async def test_updates_last_processed_at_on_success(self) -> None:
        hub = Hub()
        adapter = MagicMock()
        adapter.render_audio_stream = AsyncMock()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"x", session_id="s1", chunk_index=0, is_final=True
            )

        await hub.dispatch_audio_stream(msg, chunks())
        assert hub._last_processed_at is not None


# ---------------------------------------------------------------------------
# #256 — dispatch_voice_stream
# ---------------------------------------------------------------------------


class TestDispatchVoiceStream:
    async def test_dispatch_voice_stream_routes_to_dispatcher(self) -> None:
        # Arrange
        hub = Hub()
        adapter = MagicMock()
        hub.register_adapter(Platform.DISCORD, "main", adapter)
        dispatcher = MagicMock()
        dispatcher.enqueue_voice_stream = MagicMock()
        hub.register_outbound_dispatcher(Platform.DISCORD, "main", dispatcher)
        msg = make_inbound_message(platform="discord", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_voice_stream(msg, c)
        dispatcher.enqueue_voice_stream.assert_called_once_with(msg, c)
        assert hub._last_processed_at is not None

    async def test_dispatch_voice_stream_fallback_direct(self) -> None:
        # Arrange — no dispatcher, adapter registered
        hub = Hub()
        adapter = MagicMock()
        adapter.render_voice_stream = AsyncMock()
        hub.register_adapter(Platform.DISCORD, "main", adapter)
        msg = make_inbound_message(platform="discord", bot_id="main")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        c = chunks()
        await hub.dispatch_voice_stream(msg, c)
        adapter.render_voice_stream.assert_awaited_once()
        call_args = adapter.render_voice_stream.call_args[0]
        # chunks first, then msg (same convention as dispatch_audio_stream)
        assert call_args[0] is c
        assert call_args[1] is msg

    async def test_dispatch_voice_stream_raises_if_no_adapter(self) -> None:
        # Arrange — no dispatcher, no adapter
        hub = Hub()
        msg = make_inbound_message(platform="discord", bot_id="ghost")

        async def chunks() -> AsyncIterator[OutboundAudioChunk]:
            yield OutboundAudioChunk(
                chunk_bytes=b"pcm", session_id="s1", chunk_index=0, is_final=True
            )

        with pytest.raises(KeyError):
            await hub.dispatch_voice_stream(msg, chunks())


# ---------------------------------------------------------------------------
# T6 — Unmatched routing: log warning, message consumed, loop continues
# ---------------------------------------------------------------------------


class TestUnmatchedRouting:
    async def test_unmatched_key_logs_warning_and_continues(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        hub = Hub()
        msg = make_inbound_message(platform="telegram", user_id="nobody")
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
            Platform.TELEGRAM, "main", "chat:42", "ghost", "telegram:main:chat:42"
        )
        msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
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

        hub = Hub(rate_window=1)
        msg = make_inbound_message(platform="telegram", user_id="alice")

        # First call — inserts a timestamp for alice's user key
        hub._is_rate_limited(msg)
        key = (msg.platform, msg.bot_id, msg.user_id)
        assert key in hub._rate_timestamps
        assert len(hub._rate_timestamps[key]) == 1

        # Advance monotonic clock past the 1-second window
        now_plus_2 = hub._rate_timestamps[key][0] + 2
        with unittest.mock.patch(
            "lyra.core.hub_rate_limit.time.monotonic", return_value=now_plus_2
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


# ---------------------------------------------------------------------------
# Hub dispatch_streaming
# ---------------------------------------------------------------------------


class TestDispatchStreaming:
    async def test_dispatches_streaming_to_adapter(self) -> None:
        hub = Hub()
        received: list[str] = []

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                async for chunk in chunks:  # type: ignore[union-attr]
                    received.append(chunk)

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield "Hello"
            yield " world"

        await hub.dispatch_streaming(msg, gen())
        assert received == ["Hello", " world"]

    async def test_updates_last_processed_at_on_streaming_success(self) -> None:
        """dispatch_streaming sets _last_processed_at on successful stream."""
        hub = Hub()

        class StreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                async for _ in chunks:  # type: ignore[union-attr]
                    pass

        hub.register_adapter(Platform.TELEGRAM, "main", StreamAdapter())  # type: ignore[arg-type]
        assert hub._last_processed_at is None
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield "hi"

        await hub.dispatch_streaming(msg, gen())
        assert hub._last_processed_at is not None

    async def test_fallback_to_send_when_no_send_streaming(self) -> None:
        hub = Hub()
        sent: list[object] = []

        class LegacyAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                sent.append(outbound)

        # LegacyAdapter intentionally lacks send_streaming to test fallback
        hub.register_adapter(Platform.TELEGRAM, "main", LegacyAdapter())  # type: ignore[arg-type]
        msg = make_inbound_message(platform="telegram", bot_id="main")

        async def gen():
            yield "Hello"
            yield " world"

        await hub.dispatch_streaming(msg, gen())
        assert len(sent) == 1
        # dispatch_streaming fallback now sends OutboundMessage.from_text(text)
        assert isinstance(sent[0], OutboundMessage)
        assert "Hello world" in str(sent[0].content)


# ---------------------------------------------------------------------------
# Hub run loop with streaming agent
# ---------------------------------------------------------------------------


class TestHubRunStreaming:
    async def test_streaming_agent_dispatches_via_streaming(self) -> None:
        """Hub.run() detects async generator and calls dispatch_streaming."""
        hub = Hub()
        received_chunks: list[str] = []

        class StreamingAgent(AgentBase):
            async def process(  # type: ignore[override]
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ):
                yield "chunk1"
                yield "chunk2"

        class CapturingStreamAdapter:
            async def send(
                self, original_msg: InboundMessage, outbound: OutboundMessage
            ) -> None:
                pass

            async def send_streaming(
                self,
                original_msg: InboundMessage,
                chunks: object,
                outbound=None,
            ) -> None:
                async for chunk in chunks:  # type: ignore[union-attr]
                    received_chunks.append(chunk)

        config = Agent(name="streamer", system_prompt="", memory_namespace="lyra")
        hub.register_agent(StreamingAgent(config))
        hub.register_adapter(Platform.TELEGRAM, "main", CapturingStreamAdapter())  # type: ignore[arg-type]
        hub.register_binding(
            Platform.TELEGRAM, "main", "chat:42", "streamer", "telegram:main:chat:42"
        )

        msg = make_inbound_message(platform="telegram", bot_id="main", user_id="alice")
        await hub.bus.put(msg)

        try:
            await asyncio.wait_for(hub.run(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        assert received_chunks == ["chunk1", "chunk2"]


# ---------------------------------------------------------------------------
# Circuit breaker helpers
# ---------------------------------------------------------------------------


def make_circuit_registry(**overrides) -> CircuitRegistry:
    """Build a CircuitRegistry with default CBs for all 4 services."""
    registry = CircuitRegistry()
    defaults = {
        "anthropic": CircuitBreaker(
            "anthropic", failure_threshold=3, recovery_timeout=60
        ),
        "telegram": CircuitBreaker(
            "telegram", failure_threshold=5, recovery_timeout=30
        ),
        "discord": CircuitBreaker("discord", failure_threshold=5, recovery_timeout=30),
        "hub": CircuitBreaker("hub", failure_threshold=10, recovery_timeout=60),
    }
    for name, cb in defaults.items():
        if name in overrides:
            registry.register(overrides[name])
        else:
            registry.register(cb)
    return registry


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

    hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
    hub.register_agent(MockStreamingAgent())  # type: ignore[arg-type]
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
    import re

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

    hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
    hub.register_agent(MockStreamingAgent())  # type: ignore[arg-type]
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

    hub.register_adapter(Platform.TELEGRAM, "main", SilentAdapter())  # type: ignore[arg-type]
    hub.register_agent(CleanStreamingAgent())  # type: ignore[arg-type]
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

    hub.register_adapter(Platform.TELEGRAM, "main", SilentAdapter())  # type: ignore[arg-type]
    hub.register_agent(FailingStreamAgent())  # type: ignore[arg-type]
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

    hub.register_adapter(Platform.TELEGRAM, "main", SilentAdapter())  # type: ignore[arg-type]
    hub.register_agent(AlwaysFailStreamAgent())  # type: ignore[arg-type]
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
            async for _ in chunks:  # type: ignore[union-attr]
                pass

    class FailingAgent(AgentBase):
        async def process(
            self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
        ) -> Response:
            raise RuntimeError("simulated agent failure")

    config = Agent(name="failing", system_prompt="", memory_namespace="test")
    hub.register_agent(FailingAgent(config))
    hub.register_adapter(Platform.TELEGRAM, "main", CapturingAdapter())  # type: ignore[arg-type]
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


# ---------------------------------------------------------------------------
# T2 — Scope-based RoutingKey fields
# ---------------------------------------------------------------------------


class TestScopeIdRouting:
    """RoutingKey uses scope_id (not user_id) as the 3rd field."""

    def test_routing_key_has_scope_id_field(self) -> None:
        from lyra.core.hub import RoutingKey

        key = RoutingKey(
            platform=Platform.TELEGRAM,
            bot_id="main",
            scope_id="chat:555",
        )
        assert key.scope_id == "chat:555"
        assert not hasattr(key, "user_id")

    def test_to_pool_id_uses_scope_id(self) -> None:
        from lyra.core.hub import RoutingKey

        key = RoutingKey(
            platform=Platform.TELEGRAM,
            bot_id="main",
            scope_id="chat:555",
        )
        pool_id = key.to_pool_id()
        assert pool_id == "telegram:main:chat:555"


# ---------------------------------------------------------------------------
# T3 — Cross-scope rate limiting is per-user
# ---------------------------------------------------------------------------


class TestCrossScopeRateLimit:
    """Rate limiting is per-user regardless of scope."""

    def test_rate_limit_spans_scopes(self) -> None:
        # Arrange — two different scopes for the same user_id
        hub = Hub(rate_limit=5, rate_window=60)

        def make_scoped_message(scope_id: str) -> InboundMessage:
            return InboundMessage(
                id="msg-1",
                platform="telegram",
                bot_id="main",
                scope_id=scope_id,
                user_id="alice",
                user_name="Alice",
                is_mention=False,
                text="hello",
                text_raw="hello",
                timestamp=datetime.now(timezone.utc),
                platform_meta={
                    "chat_id": int(scope_id.split(":")[-1]),
                    "topic_id": None,
                    "message_id": None,
                    "is_group": False,
                },
                trust_level=TrustLevel.TRUSTED,
            )

        # Act — send RATE_LIMIT (5) messages, 3 from scope A and 2 from scope B
        results = []
        for _ in range(3):
            results.append(hub._is_rate_limited(make_scoped_message("chat:100")))
        for _ in range(2):
            results.append(hub._is_rate_limited(make_scoped_message("chat:200")))
        # The 6th message (RATE_LIMIT+1) must be rate-limited regardless of scope
        last_result = hub._is_rate_limited(make_scoped_message("chat:100"))

        # Assert — first 5 messages pass, 6th is rate-limited
        assert all(r is False for r in results), (
            f"Expected all first {hub._rate_limit} messages to pass, got {results}"
        )
        assert last_result is True, (
            "Expected 6th message to be rate-limited (rate limit spans scopes)"
        )
        # Key type must be a plain tuple (platform, bot_id, user_id),
        # NOT a RoutingKey — prevents scope-switching bypass.
        assert ("telegram", "main", "alice") in hub._rate_timestamps


# ---------------------------------------------------------------------------
# T4 — Scope-isolated pools
# ---------------------------------------------------------------------------


class TestScopeIsolatedPools:
    """Different scopes for the same user_id produce different Pool objects."""

    def test_scope_isolated_pools(self) -> None:
        # Arrange — wildcard binding; same user, two different chat_ids (scopes)
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        msg_a = InboundMessage(
            id="msg-a",
            platform="telegram",
            bot_id="main",
            scope_id="chat:100",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            platform_meta={
                "chat_id": 100,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
            trust_level=TrustLevel.TRUSTED,
        )
        msg_b = InboundMessage(
            id="msg-b",
            platform="telegram",
            bot_id="main",
            scope_id="chat:200",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            platform_meta={
                "chat_id": 200,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
            trust_level=TrustLevel.TRUSTED,
        )

        # Act — resolve binding and create pools for each scoped message
        binding_a = hub.resolve_binding(msg_a)
        binding_b = hub.resolve_binding(msg_b)
        assert binding_a is not None
        assert binding_b is not None

        pool_a = hub.get_or_create_pool(binding_a.pool_id, binding_a.agent_name)
        pool_b = hub.get_or_create_pool(binding_b.pool_id, binding_b.agent_name)

        # Assert — different scopes produce different pools with exact pool_ids
        assert pool_a is not pool_b, (
            "Expected different Pool objects for different scopes"
        )
        assert pool_a.pool_id == "telegram:main:chat:100"
        assert pool_b.pool_id == "telegram:main:chat:200"


# ---------------------------------------------------------------------------
# RED — #138: OutboundMessage dispatch (Slice V2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_response_accepts_outbound_message() -> None:
    """hub.dispatch_response() must accept an OutboundMessage and forward it
    to the adapter's send() method unchanged (issue #138, Slice V2)."""
    # Arrange
    hub = Hub()
    received: list[OutboundMessage] = []

    class MockAdapterV2:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            received.append(outbound)

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            pass

    hub.register_adapter(Platform.TELEGRAM, "main", MockAdapterV2())  # type: ignore[arg-type]
    msg = make_inbound_message(platform="telegram", bot_id="main")
    outbound = OutboundMessage.from_text("hi")

    # Act
    await hub.dispatch_response(msg, outbound)

    # Assert
    assert len(received) == 1
    assert received[0].content == ["hi"]


@pytest.mark.asyncio
async def test_dispatch_response_accepts_legacy_response() -> None:
    """hub.dispatch_response() must still accept a plain Response for backward
    compatibility — no call-site changes required at pool.py (issue #138, U5)."""
    # Arrange
    hub = Hub()
    received: list[Response] = []

    class LegacyCapturingAdapter:
        async def send(
            self, original_msg: InboundMessage, outbound: OutboundMessage
        ) -> None:
            received.append(outbound)  # type: ignore[arg-type]

        async def send_streaming(
            self,
            original_msg: InboundMessage,
            chunks: object,
            outbound=None,
        ) -> None:
            pass

    hub.register_adapter(Platform.TELEGRAM, "main", LegacyCapturingAdapter())  # type: ignore[arg-type]
    msg = make_inbound_message(platform="telegram", bot_id="main")

    # Act
    await hub.dispatch_response(msg, Response(content="hi"))

    # Assert — adapter received something with the original text
    assert len(received) == 1
    result = received[0]
    # After GREEN: dispatch_response converts Response → OutboundMessage internally;
    # the adapter receives an OutboundMessage.  We assert the text is preserved.
    content = result.content if isinstance(result, Response) else result.content  # type: ignore[union-attr]
    assert "hi" in str(content)


# ---------------------------------------------------------------------------
# Pool TTL eviction (#205)
# ---------------------------------------------------------------------------


class TestPoolTTLEviction:
    """PoolManager._evict_stale_pools removes idle pools exceeding the TTL."""

    @staticmethod
    def _force_eviction_eligible(hub: Hub) -> None:
        """Reset throttle so the next get_or_create_pool triggers eviction."""
        hub._pool_manager._last_eviction_check = 0.0

    def test_stale_idle_pool_evicted(self) -> None:
        """An idle pool past TTL is removed on next get_or_create_pool call."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" not in hub.pools
        assert "p2" in hub.pools

    def test_active_pool_not_evicted(self) -> None:
        """A pool with a running task is never evicted, even past TTL."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        pool._current_task = MagicMock()
        pool._current_task.done.return_value = False
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" in hub.pools  # not evicted — still active

    def test_fresh_pool_not_evicted(self) -> None:
        """A recently active idle pool is kept."""
        hub = Hub(pool_ttl=60)
        hub.get_or_create_pool("p1", "agent")
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" in hub.pools

    def test_pool_ttl_default(self) -> None:
        """Default POOL_TTL is 3600s and passes through to _pool_ttl."""
        hub = Hub()
        assert hub._pool_ttl == 3600.0

    def test_done_task_pool_evicted(self) -> None:
        """A pool whose task finished (done()=True) is evicted when stale."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        pool._current_task = MagicMock()
        pool._current_task.done.return_value = True
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p2", "agent")
        assert "p1" not in hub.pools  # evicted — task is done

    def test_multiple_stale_pools_all_evicted(self) -> None:
        """Multiple stale idle pools are evicted in a single pass."""
        hub = Hub(pool_ttl=60)
        p1 = hub.get_or_create_pool("p1", "agent")
        hub.get_or_create_pool("p2", "agent")
        p3 = hub.get_or_create_pool("p3", "agent")
        p1._last_active -= 120
        p3._last_active -= 120
        self._force_eviction_eligible(hub)
        hub.get_or_create_pool("p4", "agent")
        assert "p1" not in hub.pools
        assert "p3" not in hub.pools
        assert "p2" in hub.pools
        assert "p4" in hub.pools

    def test_eviction_throttled(self) -> None:
        """Eviction scan is throttled — skipped if less than TTL/10 elapsed."""
        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 120
        # Don't reset throttle — second call is within TTL/10
        hub.get_or_create_pool("p2", "agent")
        assert "p1" in hub.pools  # not evicted — throttled

    async def test_submit_refreshes_last_active(self) -> None:
        """Pool.submit() updates last_active timestamp."""
        import time

        hub = Hub(pool_ttl=60)
        pool = hub.get_or_create_pool("p1", "agent")
        pool._last_active -= 50  # nearly stale
        t0 = time.monotonic()
        msg = make_inbound_message()
        pool.submit(msg)
        assert pool.last_active >= t0
        pool.cancel()


# ---------------------------------------------------------------------------
# S2 — Hub._memory + _memory_tasks fields (issue #83)
#
# RED phase: these tests FAIL until Hub gains _memory and _memory_tasks fields
# and register_agent() injects the MemoryManager into each AgentBase.
# ---------------------------------------------------------------------------


class TestHubMemoryFields:
    """Hub must carry a MemoryManager reference and a set of pending tasks (S2)."""

    def test_hub_has_memory_field(self) -> None:
        """Hub must expose a _memory attribute, defaulting to None."""
        hub = Hub()
        assert hasattr(hub, "_memory")  # FAILS until field added
        assert hub._memory is None

    def test_hub_has_memory_tasks_set(self) -> None:
        """Hub must expose a _memory_tasks attribute as a set."""
        hub = Hub()
        assert hasattr(hub, "_memory_tasks")  # FAILS until field added
        assert isinstance(hub._memory_tasks, set)

    def test_register_agent_injects_memory(self) -> None:
        """register_agent() must inject hub._memory into the agent's _memory."""
        from unittest.mock import MagicMock

        hub = Hub()
        mock_mm = MagicMock()
        hub._memory = mock_mm  # type: ignore[attr-defined]

        class ConcreteAgent(AgentBase):
            async def process(  # type: ignore[override]
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ):
                pass

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = ConcreteAgent(config)
        hub.register_agent(agent)

        # After registration, agent._memory must point to hub._memory
        assert agent._memory is mock_mm  # FAILS until register_agent() injects memory

    def test_register_agent_skips_injection_when_memory_is_none(self) -> None:
        """register_agent() must not fail if hub._memory is None
        (memory not configured)."""

        class ConcreteAgent(AgentBase):
            async def process(  # type: ignore[override]
                self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
            ):
                pass

        hub = Hub()
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")
        agent = ConcreteAgent(config)
        # Must not raise even when _memory is None
        hub.register_agent(agent)


# ---------------------------------------------------------------------------
# S4 — Hub eviction creates flush tasks + shutdown drains (issue #83)
#
# RED phase: these tests FAIL until Hub._evict_stale_pools() and
# Hub.shutdown() handle memory flush tasks.
# ---------------------------------------------------------------------------


class TestHubEvictFlushTask:
    """Hub._evict_stale_pools() must create flush tasks for pools with messages (S4)."""

    @pytest.mark.asyncio
    async def test_evict_stale_pool_creates_flush_task(self) -> None:
        """Evicting a pool that has at least one message must schedule a flush task."""
        from unittest.mock import AsyncMock

        hub = Hub(pool_ttl=1)
        mock_mm = AsyncMock()
        hub._memory = mock_mm  # type: ignore[attr-defined]

        # Register a mock agent with flush_session so eviction creates a task
        mock_agent = MagicMock()
        mock_agent.name = "agent"
        mock_agent.flush_session = AsyncMock()
        hub.register_agent(mock_agent)

        pool = hub.get_or_create_pool("p_flush", "agent")
        pool._last_active -= 5  # force stale
        # Simulate the pool having had a message (user_id is set)
        pool.user_id = "u1"  # type: ignore[attr-defined]

        # Reset throttle and trigger eviction scan
        hub._pool_manager._last_eviction_check = 0.0
        hub._pool_manager._evict_stale_pools()  # type: ignore[attr-defined]

        # Eviction must schedule a flush task for the pool with messages
        assert len(hub._memory_tasks) >= 1

    @pytest.mark.asyncio
    async def test_shutdown_has_shutdown_method(self) -> None:
        """Hub must expose a shutdown() coroutine method (S4)."""
        hub = Hub()
        assert hasattr(hub, "shutdown")  # FAILS until shutdown() is added


# ---------------------------------------------------------------------------
# SC-10 — record_circuit_failure records anthropic CB for ProviderError subclasses
# ---------------------------------------------------------------------------


class TestRecordCircuitFailure:
    def test_provider_error_subclass_records_anthropic_cb(self) -> None:
        """ProviderAuthError (subclass) trips both hub and anthropic CBs."""
        from lyra.errors import ProviderAuthError

        hub_cb = CircuitBreaker("hub", failure_threshold=5)
        ant_cb = CircuitBreaker("anthropic", failure_threshold=5)
        registry = CircuitRegistry()
        registry.register(hub_cb)
        registry.register(ant_cb)
        hub = Hub(circuit_registry=registry)

        hub.record_circuit_failure(ProviderAuthError("bad key", status_code=401))

        assert hub_cb._failure_count == 1
        assert ant_cb._failure_count == 1

    def test_runtime_error_does_not_record_anthropic_cb(self) -> None:
        """Plain RuntimeError trips hub CB only, not anthropic CB."""
        hub_cb = CircuitBreaker("hub", failure_threshold=5)
        ant_cb = CircuitBreaker("anthropic", failure_threshold=5)
        registry = CircuitRegistry()
        registry.register(hub_cb)
        registry.register(ant_cb)
        hub = Hub(circuit_registry=registry)

        hub.record_circuit_failure(RuntimeError("boom"))

        assert hub_cb._failure_count == 1
        assert ant_cb._failure_count == 0
