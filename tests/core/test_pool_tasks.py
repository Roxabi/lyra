"""Pool task lifecycle tests — submit, idle exit, sequential, timeout, cancel,
exception handling.

Spec trace: S4-1, S4-2, S4-3, S4-4, S4-5, S4-6, S4-7
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from tests.core.conftest import FastAgent, SlowAgent, _drain, make_msg

# ---------------------------------------------------------------------------
# File-local agent doubles
# ---------------------------------------------------------------------------


class RaisingAgent:
    """Agent that raises an unhandled exception."""

    name = "test_agent"

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        raise RuntimeError("boom")


class TwoMsgAgent:
    """Records call order; returns a sequenced response."""

    name = "test_agent"
    calls: list[str]

    def __init__(self) -> None:
        self.calls = []

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        self.calls.append(msg.text)
        return Response(content=f"reply:{msg.text}")


# ---------------------------------------------------------------------------
# S4-2 — Pool.submit() creates a task
# ---------------------------------------------------------------------------


class TestPoolSubmit:
    """Pool.submit() enqueues and starts a processing task (S4-1, S4-2)."""

    @pytest.mark.asyncio
    async def test_pool_submit_creates_task(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """submit() when idle sets _current_task to a running asyncio.Task."""
        agent = FastAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg("hello")

        pool.submit(msg)

        assert pool._current_task is not None
        assert isinstance(pool._current_task, asyncio.Task)

        await _drain(pool)

    @pytest.mark.asyncio
    async def test_pool_submit_reuses_running_task(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """Second submit() while task is running does NOT create a new task object."""
        agent = FastAgent()
        ctx_mock._agents["test_agent"] = agent
        msg1 = make_msg("first")
        msg2 = make_msg("second")

        pool.submit(msg1)
        first_task = pool._current_task
        pool.submit(msg2)
        second_task = pool._current_task

        assert first_task is second_task

        await _drain(pool)


# ---------------------------------------------------------------------------
# S4-6 — Task exits on idle
# ---------------------------------------------------------------------------


class TestPoolTaskExitsOnIdle:
    """_current_task is set to None after the inbox drains (S4-6)."""

    @pytest.mark.asyncio
    async def test_pool_task_exits_on_idle(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """After processing all messages, _current_task becomes None."""
        agent = FastAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        pool.submit(msg)
        await _drain(pool)

        assert pool._current_task is None


# ---------------------------------------------------------------------------
# S4-3 — Sequential processing
# ---------------------------------------------------------------------------


class TestPoolSequentialProcessing:
    """Messages within a pool are processed in submission order (S4-3)."""

    @pytest.mark.asyncio
    async def test_pool_sequential_processing(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """Two messages submitted before processing starts are batched together.

        With the debouncer (even at debounce_ms=0), messages already on the
        inbox queue are drained in one batch. The agent receives a single
        merged message with both texts newline-joined.
        """
        agent = TwoMsgAgent()
        ctx_mock._agents["test_agent"] = agent
        msg1 = make_msg("first")
        msg2 = make_msg("second")

        pool.submit(msg1)
        pool.submit(msg2)
        await _drain(pool, timeout=3.0)

        assert ctx_mock.dispatch_response.await_count == 1
        assert len(agent.calls) == 1
        assert "first" in agent.calls[0]
        assert "second" in agent.calls[0]

    @pytest.mark.asyncio
    async def test_pool_sequential_across_turns(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """Messages submitted in separate turns are processed sequentially."""
        agent = TwoMsgAgent()
        ctx_mock._agents["test_agent"] = agent
        msg1 = make_msg("first")
        msg2 = make_msg("second")

        pool.submit(msg1)
        await _drain(pool, timeout=3.0)

        pool.submit(msg2)
        await _drain(pool, timeout=3.0)

        assert ctx_mock.dispatch_response.await_count == 2
        assert agent.calls == ["first", "second"]


# ---------------------------------------------------------------------------
# S4-4 — Per-turn timeout
# ---------------------------------------------------------------------------


class TestPoolTimeout:
    """Timeout fires after turn_timeout seconds; sends timeout reply (S4-4)."""

    @pytest.mark.asyncio
    async def test_pool_timeout_sends_timeout_reply(
        self, fast_pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """Slow agent triggers TimeoutError; dispatch_response called with timeout."""
        agent = SlowAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        fast_pool.submit(msg)
        await _drain(fast_pool, timeout=2.0)

        ctx_mock.dispatch_response.assert_awaited_once()
        _args = ctx_mock.dispatch_response.call_args
        response_arg: Response = _args[0][1]
        content_lower = response_arg.content.lower()
        assert "timed out" in content_lower or "timeout" in content_lower

    @pytest.mark.asyncio
    async def test_pool_timeout_logs_dead_backend_error(
        self, fast_pool: Pool, ctx_mock: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Timeout with dead backend logs ERROR distinguishing from normal timeout."""
        agent = SlowAgent()
        object.__setattr__(agent, "is_backend_alive", lambda pool_id: False)
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool_processor"):
            fast_pool.submit(msg)
            await _drain(fast_pool, timeout=2.0)

        assert any("backend process died" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_pool_timeout_does_not_log_dead_backend_when_alive(
        self, fast_pool: Pool, ctx_mock: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Normal timeout (live backend) does NOT log the dead-backend ERROR."""
        agent = SlowAgent()  # is_backend_alive returns True by default
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool_processor"):
            fast_pool.submit(msg)
            await _drain(fast_pool, timeout=2.0)

        assert not any("backend process died" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_pool_history_not_updated_on_timeout(
        self, fast_pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """After a timeout, pool.history is unchanged (partial result not recorded)."""
        agent = SlowAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()
        initial_history_len = len(fast_pool.history)

        fast_pool.submit(msg)
        await _drain(fast_pool, timeout=2.0)

        assert len(fast_pool.history) == initial_history_len


# ---------------------------------------------------------------------------
# S4-5 — /stop cancellation
# ---------------------------------------------------------------------------


class TestPoolCancel:
    """pool.cancel() cancels the running task and sends a cancelled reply (S4-5)."""

    @pytest.mark.asyncio
    async def test_pool_cancel_sends_cancelled_reply(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """cancel() while processing; dispatch_response called with cancelled msg."""
        agent = SlowAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        pool.submit(msg)
        await asyncio.sleep(0)  # let process_loop start and reach wait_for
        pool.cancel()

        if pool._current_task is not None:
            try:
                await asyncio.wait_for(pool._current_task, timeout=2.0)
            except (asyncio.CancelledError, Exception):
                pass

        ctx_mock.dispatch_response.assert_awaited_once()
        _args = ctx_mock.dispatch_response.call_args
        response_arg: Response = _args[0][1]
        assert len(response_arg.content) > 0  # non-empty cancelled reply

    @pytest.mark.asyncio
    async def test_pool_cancel_noop_when_idle(self, pool: Pool) -> None:
        """cancel() when _current_task is None does not raise."""
        assert pool._current_task is None
        pool.cancel()


# ---------------------------------------------------------------------------
# S4-7 — Unhandled exception: loop continues
# ---------------------------------------------------------------------------


class TestPoolExceptionHandling:
    """Unhandled exception sends generic reply and loop continues (S4-7)."""

    @pytest.mark.asyncio
    async def test_pool_exception_sends_generic_reply_and_continues(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """process() raises; generic reply sent; loop continues to next message."""
        raising_agent = RaisingAgent()
        fast_agent = FastAgent()
        ctx_mock._agents["test_agent"] = raising_agent

        msg1 = make_msg("bad")
        msg2 = make_msg("good")

        pool.submit(msg1)

        await asyncio.sleep(0)

        ctx_mock._agents["test_agent"] = fast_agent
        pool.submit(msg2)

        await _drain(pool, timeout=3.0)

        assert ctx_mock.dispatch_response.await_count == 2

        first_call_response: Response = ctx_mock.dispatch_response.call_args_list[0][0][
            1
        ]
        assert isinstance(first_call_response, Response)
        assert len(first_call_response.content) > 0

        second_call_response: Response = ctx_mock.dispatch_response.call_args_list[1][
            0
        ][1]
        content = second_call_response.content
        assert "good" in content.lower() or content != ""
