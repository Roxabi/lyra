"""Tests for Pool + debouncer integration — burst aggregation and cancel-in-flight.

Issue #145.
"""

from __future__ import annotations

import asyncio

import pytest

from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.pool import Pool
from tests.conftest import TIMEOUT_SLOW, _drain
from tests.core.conftest import (
    RecordingAgent,
    SlowAgent,
    _make_ctx_mock,
    make_debouncer_msg,
)

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
        pool.submit(make_debouncer_msg("hey"))
        pool.submit(make_debouncer_msg("can you"))
        pool.submit(make_debouncer_msg("explain X?"))

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

        pool.submit(make_debouncer_msg("hello"))
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

        pool.submit(make_debouncer_msg("first"))
        pool.submit(make_debouncer_msg("second"))

        await _drain(pool)

        # With zero debounce, messages queued before task starts are still
        # drained together by collect(). Both arrive before the first
        # collect() call, so they get merged into one.
        assert len(agent.calls) == 1
        assert "first" in agent.calls[0]
        assert "second" in agent.calls[0]


class TestPoolNoAgent:
    """Pool handles missing agent gracefully."""

    @pytest.mark.asyncio
    async def test_no_agent_drains_inbox_and_exits(self) -> None:
        """When agent is None, all queued messages are drained and loop exits."""
        ctx = _make_ctx_mock()  # no agents registered
        pool = Pool(
            pool_id="test:main:chat:noagent",
            agent_name="missing_agent",
            ctx=ctx,
            debounce_ms=0,
        )

        pool.submit(make_debouncer_msg("first"))
        pool.submit(make_debouncer_msg("second"))
        pool.submit(make_debouncer_msg("third"))

        await _drain(pool)

        # No dispatch should have been called — agent was None.
        ctx.dispatch_response.assert_not_awaited()
        # Inbox should be fully drained.
        assert pool._inbox.empty()


class TestPoolCancelInFlight:
    """Pool cancels in-flight LLM task when new messages arrive."""

    @pytest.mark.asyncio
    async def test_cancel_in_flight_redispatches_combined(self) -> None:
        """New message during LLM processing cancels and re-dispatches."""
        agent = RecordingAgent()
        call_count = 0
        started = asyncio.Event()
        never_returns = (
            asyncio.Event()
        )  # never set — simulates slow/infinite processing
        original_process = agent.process

        async def _slow_then_fast(
            msg: InboundMessage, pool: Pool, *, on_intermediate=None
        ) -> Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Signal that the first call has started, then block.
                started.set()
                await never_returns.wait()  # explicit: never completes
                return Response(content="should not reach")
            # Second call: fast — combined message
            return await original_process(msg, pool, on_intermediate=on_intermediate)

        object.__setattr__(agent, "process", _slow_then_fast)

        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(
            pool_id="test:main:chat:cancel",
            agent_name="test_agent",
            ctx=ctx,
            debounce_ms=50,
            cancel_on_new_message=True,
        )

        # Submit first message — agent starts slow processing.
        pool.submit(make_debouncer_msg("explain X"))
        await started.wait()  # deterministic: agent is in-flight

        # Submit second message while agent is in-flight → cancel + re-dispatch.
        pool.submit(make_debouncer_msg("actually explain Y"))

        await _drain(pool, timeout=TIMEOUT_SLOW)

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

        pool.submit(make_debouncer_msg("long request"))
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
