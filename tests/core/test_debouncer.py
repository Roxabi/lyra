"""Tests for MessageDebouncer — hybrid typing aggregation (issue #145).

Covers:
  - Burst aggregation: rapid messages merged into one
  - Cancel-and-restart: in-flight LLM task cancelled on new message
  - Single message: dispatched after debounce window with no extra latency
  - Merge semantics: text join, attachment concatenation, is_mention union
  - Zero-debounce mode: no wait, immediate dispatch
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.debouncer import MessageDebouncer
from lyra.core.message import Attachment, InboundMessage, Response
from lyra.core.pool import Pool

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_msg(
    text: str = "hello",
    msg_id: str = "msg-1",
    is_mention: bool = False,
    attachments: list[Attachment] | None = None,
) -> InboundMessage:
    """Build a minimal InboundMessage for testing."""
    return InboundMessage(
        id=msg_id,
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=is_mention,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 1,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        attachments=attachments or [],
    )


def _make_ctx_mock(agents: dict | None = None) -> MagicMock:
    """Build a minimal PoolContext mock."""
    ctx = MagicMock()
    _agents: dict = agents or {}
    ctx.get_agent = MagicMock(side_effect=lambda name: _agents.get(name))
    ctx.get_message = MagicMock(return_value=None)
    ctx.dispatch_response = AsyncMock(return_value=None)
    ctx.dispatch_streaming = AsyncMock(return_value=None)
    ctx.record_circuit_success = MagicMock()
    ctx.record_circuit_failure = MagicMock()
    ctx._agents = _agents
    return ctx


class SlowAgent:
    """Agent that sleeps long enough to be interrupted."""

    name = "test_agent"

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        await asyncio.sleep(10)
        return Response(content="done")


class FastAgent:
    """Agent that echoes the message text immediately."""

    name = "test_agent"

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        return Response(content=f"echo: {msg.text}")


class RecordingAgent:
    """Agent that records the text it receives."""

    name = "test_agent"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        self.calls.append(msg.text)
        return Response(content=f"reply:{msg.text}")


async def _drain(pool: Pool, *, timeout: float = 3.0) -> None:
    """Yield to the event loop then wait for the pool task to finish."""
    await asyncio.sleep(0)
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


# ---------------------------------------------------------------------------
# MessageDebouncer.merge() — pure function tests
# ---------------------------------------------------------------------------


class TestMerge:
    """MessageDebouncer.merge() semantics."""

    def test_single_message_unchanged(self) -> None:
        """A single message is returned as-is (identity)."""
        msg = make_msg("hello")
        result = MessageDebouncer.merge([msg])
        assert result is msg

    def test_two_messages_text_joined(self) -> None:
        """Two messages produce newline-joined text."""
        m1 = make_msg("hello")
        m2 = make_msg("world")
        result = MessageDebouncer.merge([m1, m2])
        assert result.text == "hello\nworld"
        assert result.text_raw == "hello\nworld"

    def test_three_messages_text_joined(self) -> None:
        """Three messages produce newline-joined text."""
        msgs = [make_msg("a"), make_msg("b"), make_msg("c")]
        result = MessageDebouncer.merge(msgs)
        assert result.text == "a\nb\nc"

    def test_merge_uses_last_message_metadata(self) -> None:
        """Merged result uses id and timestamp from the last message."""
        m1 = make_msg("first", msg_id="msg-1")
        m2 = make_msg("second", msg_id="msg-2")
        result = MessageDebouncer.merge([m1, m2])
        assert result.id == "msg-2"

    def test_merge_is_mention_union(self) -> None:
        """is_mention is True if any constituent message was a mention."""
        m1 = make_msg("hey", is_mention=False)
        m2 = make_msg("@bot", is_mention=True)
        result = MessageDebouncer.merge([m1, m2])
        assert result.is_mention is True

    def test_merge_is_mention_all_false(self) -> None:
        """is_mention is False if no constituent message was a mention."""
        m1 = make_msg("hey", is_mention=False)
        m2 = make_msg("there", is_mention=False)
        result = MessageDebouncer.merge([m1, m2])
        assert result.is_mention is False

    def test_merge_concatenates_attachments(self) -> None:
        """Attachments from all messages are concatenated."""
        a1 = Attachment(
            type="image", url_or_path_or_bytes="url1", mime_type="image/png"
        )
        a2 = Attachment(
            type="file", url_or_path_or_bytes="url2", mime_type="text/plain"
        )
        m1 = make_msg("with image", attachments=[a1])
        m2 = make_msg("with file", attachments=[a2])
        result = MessageDebouncer.merge([m1, m2])
        assert len(result.attachments) == 2
        assert result.attachments[0] is a1
        assert result.attachments[1] is a2


# ---------------------------------------------------------------------------
# MessageDebouncer.collect() — async collection tests
# ---------------------------------------------------------------------------


class TestCollect:
    """MessageDebouncer.collect() debounce window behaviour."""

    @pytest.mark.asyncio
    async def test_single_message_returns_after_debounce(self) -> None:
        """A single message is returned after the debounce window expires."""
        debouncer = MessageDebouncer(debounce_ms=50)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        msg = make_msg("hello")
        inbox.put_nowait(msg)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=2.0)
        assert result == [msg]

    @pytest.mark.asyncio
    async def test_burst_aggregated(self) -> None:
        """Rapid messages within the debounce window are aggregated."""
        debouncer = MessageDebouncer(debounce_ms=200)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()

        m1 = make_msg("first")
        m2 = make_msg("second")
        m3 = make_msg("third")

        # Pre-fill the queue (all arrive before collect starts).
        inbox.put_nowait(m1)
        inbox.put_nowait(m2)
        inbox.put_nowait(m3)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=2.0)
        assert len(result) == 3
        assert result == [m1, m2, m3]

    @pytest.mark.asyncio
    async def test_zero_debounce_no_wait(self) -> None:
        """debounce_ms=0 drains immediately without waiting."""
        debouncer = MessageDebouncer(debounce_ms=0)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        msg = make_msg("fast")
        inbox.put_nowait(msg)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=0.5)
        assert result == [msg]

    @pytest.mark.asyncio
    async def test_zero_debounce_drains_queued(self) -> None:
        """debounce_ms=0 still drains whatever is already on the queue."""
        debouncer = MessageDebouncer(debounce_ms=0)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()
        m1 = make_msg("a")
        m2 = make_msg("b")
        inbox.put_nowait(m1)
        inbox.put_nowait(m2)

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=0.5)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_delayed_message_outside_window(self) -> None:
        """A message arriving after the debounce window is NOT aggregated."""
        debouncer = MessageDebouncer(debounce_ms=50)
        inbox: asyncio.Queue[InboundMessage] = asyncio.Queue()

        m1 = make_msg("first")
        m2 = make_msg("second")

        inbox.put_nowait(m1)

        async def _delay_put() -> None:
            await asyncio.sleep(0.15)  # well after 50ms window
            inbox.put_nowait(m2)

        asyncio.create_task(_delay_put())

        result = await asyncio.wait_for(debouncer.collect(inbox), timeout=2.0)
        assert len(result) == 1
        assert result[0] is m1


# ---------------------------------------------------------------------------
# Pool integration — debounce + cancel-in-flight
# ---------------------------------------------------------------------------


class TestPoolDebounce:
    """Pool uses debouncer to aggregate rapid messages."""

    @pytest.mark.asyncio
    async def test_burst_messages_merged_before_agent(self) -> None:
        """Rapid messages are merged into one before reaching the agent."""
        agent = RecordingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(
            pool_id="test:main:chat:debounce",
            agent_name="test_agent",
            ctx=ctx,
            debounce_ms=100,
        )

        # Submit three messages rapidly — they should be aggregated.
        pool.submit(make_msg("hey"))
        pool.submit(make_msg("can you"))
        pool.submit(make_msg("explain X?"))

        await _drain(pool)

        # Agent should have been called once with the combined text.
        assert len(agent.calls) == 1
        assert "hey" in agent.calls[0]
        assert "explain X?" in agent.calls[0]

    @pytest.mark.asyncio
    async def test_single_message_no_extra_latency(self) -> None:
        """A single message is dispatched without unnecessary overhead."""
        agent = RecordingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(
            pool_id="test:main:chat:single",
            agent_name="test_agent",
            ctx=ctx,
            debounce_ms=50,
        )

        pool.submit(make_msg("hello"))
        await _drain(pool)

        assert len(agent.calls) == 1
        assert agent.calls[0] == "hello"

    @pytest.mark.asyncio
    async def test_zero_debounce_preserves_original_behaviour(self) -> None:
        """debounce_ms=0 processes messages without debounce delay."""
        agent = RecordingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(
            pool_id="test:main:chat:nodebounce",
            agent_name="test_agent",
            ctx=ctx,
            debounce_ms=0,
        )

        pool.submit(make_msg("first"))
        pool.submit(make_msg("second"))

        await _drain(pool)

        # With zero debounce, messages queued before task starts are still
        # drained together by collect(). Both arrive before the first
        # collect() call, so they get merged.
        assert len(agent.calls) >= 1


class TestPoolCancelInFlight:
    """Pool cancels in-flight LLM task when new messages arrive."""

    @pytest.mark.asyncio
    async def test_cancel_in_flight_redispatches_combined(self) -> None:
        """New message during LLM processing cancels and re-dispatches."""
        agent = RecordingAgent()
        call_count = 0
        original_process = agent.process

        async def _slow_then_fast(msg: InboundMessage, pool: Pool) -> Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: slow — will be cancelled
                await asyncio.sleep(10)
                return Response(content="should not reach")
            # Second call: fast — combined message
            return await original_process(msg, pool)

        agent.process = _slow_then_fast  # type: ignore[assignment]

        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(
            pool_id="test:main:chat:cancel",
            agent_name="test_agent",
            ctx=ctx,
            debounce_ms=50,
        )

        # Submit first message — agent starts slow processing.
        pool.submit(make_msg("explain X"))
        await asyncio.sleep(0.05)  # let the agent task start

        # Submit second message while agent is in-flight → cancel + re-dispatch.
        pool.submit(make_msg("actually explain Y"))

        await _drain(pool, timeout=5.0)

        # Agent called twice: first cancelled, second with combined.
        assert call_count == 2
        assert len(agent.calls) == 1  # only the second call completed
        assert "explain X" in agent.calls[0]
        assert "actually explain Y" in agent.calls[0]

    @pytest.mark.asyncio
    async def test_stop_cancellation_still_works(self) -> None:
        """/stop (pool.cancel()) still sends a cancelled reply."""
        agent = SlowAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(
            pool_id="test:main:chat:stop",
            agent_name="test_agent",
            ctx=ctx,
            debounce_ms=50,
        )

        pool.submit(make_msg("long request"))
        await asyncio.sleep(0.1)  # let the agent start
        pool.cancel()

        if pool._current_task is not None:
            try:
                await asyncio.wait_for(pool._current_task, timeout=3.0)
            except (asyncio.CancelledError, Exception):
                pass

        # Should have dispatched a cancellation reply.
        ctx.dispatch_response.assert_awaited()
        response_arg: Response = ctx.dispatch_response.call_args[0][1]
        assert len(response_arg.content) > 0


# ---------------------------------------------------------------------------
# RuntimeConfig — debounce_ms validation
# ---------------------------------------------------------------------------


class TestRuntimeConfigDebounceMs:
    """debounce_ms field in RuntimeConfig."""

    def test_default_value(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig

        rc = RuntimeConfig()
        assert rc.debounce_ms == 300

    def test_set_valid(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        rc = set_param(rc, "debounce_ms", "500")
        assert rc.debounce_ms == 500

    def test_set_zero(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        rc = set_param(rc, "debounce_ms", "0")
        assert rc.debounce_ms == 0

    def test_reject_negative(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        with pytest.raises(ValueError, match="between 0 and 5000"):
            set_param(rc, "debounce_ms", "-1")

    def test_reject_too_large(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        with pytest.raises(ValueError, match="between 0 and 5000"):
            set_param(rc, "debounce_ms", "6000")

    def test_reject_non_integer(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = RuntimeConfig()
        with pytest.raises(ValueError, match="integer"):
            set_param(rc, "debounce_ms", "abc")

    def test_reset_to_default(self) -> None:
        from lyra.core.runtime_config import RuntimeConfig, set_param

        rc = set_param(RuntimeConfig(), "debounce_ms", "500")
        rc = RuntimeConfig.reset(rc, "debounce_ms")
        assert rc.debounce_ms == 300
