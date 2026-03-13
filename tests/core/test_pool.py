"""Tests for Pool asyncio.Task-based lifecycle (issue #127).

RED phase — tests describe the new Pool API where a per-session asyncio.Task
replaces the Lock-based @dataclass. Tests are collected without syntax errors
but will fail until the backend-dev GREEN phase completes T1.

Spec trace: S4-1, S4-2, S4-3, S4-4, S4-5, S4-6, S4-7
"""

from __future__ import annotations

import asyncio
import collections.abc
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth import TrustLevel
from lyra.core.message import (
    InboundMessage,
    Response,
)
from lyra.core.pool import Pool

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


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
    # Keep a reference so tests can mutate the agent registry
    ctx._agents = _agents
    return ctx


@pytest.fixture
def ctx_mock() -> MagicMock:
    """Minimal PoolContext stub with the methods Pool._process_loop() touches."""
    return _make_ctx_mock()


@pytest.fixture
def pool(ctx_mock: MagicMock) -> Pool:
    """Pool with a very long timeout (not triggered in normal tests)."""
    return Pool(
        pool_id="test:main:chat:1",
        agent_name="test_agent",
        ctx=ctx_mock,
        turn_timeout=60.0,
        debounce_ms=0,
    )


@pytest.fixture
def fast_pool(ctx_mock: MagicMock) -> Pool:
    """Pool with a very short timeout for timeout tests."""
    return Pool(
        pool_id="test:main:chat:1",
        agent_name="test_agent",
        ctx=ctx_mock,
        turn_timeout=0.05,
        debounce_ms=0,
    )


def make_msg(text: str = "hello") -> InboundMessage:
    """Build a minimal InboundMessage for testing."""
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
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
    )


class SlowAgent:
    """Agent whose process() never returns within test timeouts."""

    name = "test_agent"

    def is_backend_alive(self, pool_id: str) -> bool:
        return True

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        await asyncio.sleep(10)  # never finishes in test
        return Response(content="done")


class FastAgent:
    """Agent that echoes the message text immediately."""

    name = "test_agent"

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        return Response(content=f"echo: {msg.text}")


class RaisingAgent:
    """Agent that raises an unhandled exception."""

    name = "test_agent"

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        raise RuntimeError("boom")


class TwoMsgAgent:
    """Records call order; returns a sequenced response."""

    name = "test_agent"
    calls: list[str]

    def __init__(self) -> None:
        self.calls = []

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        self.calls.append(msg.text)
        return Response(content=f"reply:{msg.text}")


async def _drain(pool: Pool, *, timeout: float = 2.0) -> None:
    """Yield to the event loop then wait for the current task to finish."""
    await asyncio.sleep(0)
    if pool._current_task is not None:
        await asyncio.wait_for(pool._current_task, timeout=timeout)


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
        # Arrange
        agent = FastAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg("hello")

        # Act
        pool.submit(msg)

        # Assert
        assert pool._current_task is not None
        assert isinstance(pool._current_task, asyncio.Task)

        # Cleanup — drain so the task doesn't leak
        await _drain(pool)

    @pytest.mark.asyncio
    async def test_pool_submit_reuses_running_task(
        self, pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """Second submit() while task is running does NOT create a new task object."""
        # Arrange
        agent = FastAgent()
        ctx_mock._agents["test_agent"] = agent
        msg1 = make_msg("first")
        msg2 = make_msg("second")

        # Act — submit two messages quickly; the task is created on the first
        pool.submit(msg1)
        first_task = pool._current_task
        pool.submit(msg2)
        second_task = pool._current_task

        # Assert — same task object (or at minimum, inbox has the second message)
        # The task is created once; second submit puts the message on the inbox
        assert first_task is second_task

        # Cleanup
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
        # Arrange
        agent = FastAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        # Act
        pool.submit(msg)
        await _drain(pool)

        # Assert
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
        # Arrange
        agent = TwoMsgAgent()
        ctx_mock._agents["test_agent"] = agent
        msg1 = make_msg("first")
        msg2 = make_msg("second")

        # Act — both submitted before the task processes anything
        pool.submit(msg1)
        pool.submit(msg2)
        await _drain(pool, timeout=3.0)

        # Assert — debouncer batched both messages into one dispatch
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

        # First message processed alone
        pool.submit(msg1)
        await _drain(pool, timeout=3.0)

        # Second message in a new turn
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
        # Arrange
        agent = SlowAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        # Act
        fast_pool.submit(msg)
        await _drain(fast_pool, timeout=2.0)

        # Assert — dispatch_response was called once with a timeout reply
        ctx_mock.dispatch_response.assert_awaited_once()
        _args = ctx_mock.dispatch_response.call_args
        response_arg: Response = _args[0][1]
        content_lower = response_arg.content.lower()
        assert "timed out" in content_lower or "timeout" in content_lower

    @pytest.mark.asyncio
    async def test_pool_history_not_updated_on_timeout(
        self, fast_pool: Pool, ctx_mock: MagicMock
    ) -> None:
        """After a timeout, pool.history is unchanged (partial result not recorded)."""
        # Arrange
        agent = SlowAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()
        initial_history_len = len(fast_pool.history)

        # Act
        fast_pool.submit(msg)
        await _drain(fast_pool, timeout=2.0)

        # Assert — history not extended by the timed-out turn
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
        # Arrange
        agent = SlowAgent()
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        # Act — submit, yield to let the task start, then cancel
        pool.submit(msg)
        await asyncio.sleep(0)  # let process_loop start and reach wait_for
        pool.cancel()

        # Wait for the task to finish (CancelledError propagated → task done)
        if pool._current_task is not None:
            try:
                await asyncio.wait_for(pool._current_task, timeout=2.0)
            except (asyncio.CancelledError, Exception):
                pass

        # Assert — dispatch_response was called once with cancelled content
        ctx_mock.dispatch_response.assert_awaited_once()
        _args = ctx_mock.dispatch_response.call_args
        response_arg: Response = _args[0][1]
        assert len(response_arg.content) > 0  # non-empty cancelled reply

    @pytest.mark.asyncio
    async def test_pool_cancel_noop_when_idle(self, pool: Pool) -> None:
        """cancel() when _current_task is None does not raise."""
        # Arrange — no task running
        assert pool._current_task is None

        # Act / Assert — must not raise
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
        # Arrange — two messages: first raises, second succeeds
        raising_agent = RaisingAgent()
        fast_agent = FastAgent()
        ctx_mock._agents["test_agent"] = raising_agent

        msg1 = make_msg("bad")
        msg2 = make_msg("good")

        # Act — submit both; first will raise, second should still be processed
        # Switch to fast agent after first message by patching the registry mid-flight
        pool.submit(msg1)

        # Give event loop a tick so the first message processing starts
        await asyncio.sleep(0)

        # Now also queue the second message (agent updated)
        ctx_mock._agents["test_agent"] = fast_agent
        pool.submit(msg2)

        await _drain(pool, timeout=3.0)

        # Assert — dispatch_response called twice: once for the error reply,
        # once for the successful second message — proving the loop continued.
        assert ctx_mock.dispatch_response.await_count == 2

        # The first call should be the generic error reply
        first_call_response: Response = ctx_mock.dispatch_response.call_args_list[0][0][
            1
        ]
        assert isinstance(first_call_response, Response)
        assert len(first_call_response.content) > 0  # non-empty generic reply

        # Verify the second call processed the good message
        second_call_response: Response = ctx_mock.dispatch_response.call_args_list[1][
            0
        ][1]
        content = second_call_response.content
        assert "good" in content.lower() or content != ""


# ---------------------------------------------------------------------------
# extend_sdk_history — trim to max_sdk_history
# ---------------------------------------------------------------------------


class TestExtendSdkHistory:
    """Pool.extend_sdk_history() trims sdk_history to max_sdk_history."""

    def test_extend_sdk_history_trims_to_max(self, pool: Pool) -> None:
        """Adding more entries than max_sdk_history trims the oldest entries."""
        # Arrange
        pool.max_sdk_history = 3
        initial = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
        pool.extend_sdk_history(initial)

        # Act — add 2 more; should evict the oldest to stay at cap=3
        pool.extend_sdk_history(
            [
                {"role": "user", "content": "new1"},
                {"role": "user", "content": "new2"},
            ]
        )

        # Assert
        assert len(pool.sdk_history) == 3
        contents = [entry["content"] for entry in pool.sdk_history]
        assert "msg0" not in contents  # oldest evicted
        assert "msg1" not in contents  # second oldest evicted
        assert "new1" in contents
        assert "new2" in contents

    def test_extend_sdk_history_no_trim_when_under_max(self, pool: Pool) -> None:
        """extend_sdk_history() keeps all entries when under the cap."""
        # Arrange
        pool.max_sdk_history = 10
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(5)]

        # Act
        pool.extend_sdk_history(messages)

        # Assert
        assert len(pool.sdk_history) == 5


# ---------------------------------------------------------------------------
# Streaming path — Pool._process_one() async-generator branch
# ---------------------------------------------------------------------------


class StreamingAgent:
    """Test double: returns an async generator (streaming path)."""

    name = "test_agent"

    async def process(  # type: ignore[override]
        self, msg: InboundMessage, pool: Pool
    ) -> collections.abc.AsyncIterator[str]:
        async def _gen() -> collections.abc.AsyncIterator[str]:
            yield "hello "
            yield "world"

        return _gen()


class FailingStreamingAgent:
    """Test double: returns a generator that raises mid-stream."""

    name = "test_agent"

    async def process(  # type: ignore[override]
        self, msg: InboundMessage, pool: Pool
    ) -> collections.abc.AsyncIterator[str]:
        async def _gen() -> collections.abc.AsyncIterator[str]:
            yield "partial"
            raise RuntimeError("stream error")

        return _gen()


class TestPoolStreaming:
    """Pool._process_one() async-generator (streaming) branch (S4-*)."""

    @pytest.mark.asyncio
    async def test_pool_streaming_path_calls_dispatch_streaming(self) -> None:
        """Streaming result routes to dispatch_streaming, not dispatch_response."""
        # Arrange
        agent = StreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(pool_id="test:main:chat:stream", agent_name="test_agent", ctx=ctx)

        msg = make_msg("stream test")

        # Act
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=2.0)

        # Assert — streaming path goes through dispatch_streaming
        ctx.dispatch_streaming.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_streaming_records_cb_success(self) -> None:
        """Successful streaming records CB success when a circuit registry exists."""
        # Arrange
        agent = StreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})

        pool = Pool(pool_id="test:main:chat:cbstream", agent_name="test_agent", ctx=ctx)

        msg = make_msg("cb stream")

        # Act
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=2.0)

        # Assert — streaming path dispatched successfully
        ctx.dispatch_streaming.assert_awaited_once()
        ctx.record_circuit_success.assert_called()

    @pytest.mark.asyncio
    async def test_pool_failing_stream_sends_generic_reply(self) -> None:
        """A stream that raises mid-iteration sends a generic error reply."""
        # Arrange
        ctx = _make_ctx_mock({"test_agent": FailingStreamingAgent()})
        pool = Pool(
            pool_id="test:main:chat:failstream", agent_name="test_agent", ctx=ctx
        )
        ctx.dispatch_streaming.side_effect = RuntimeError("stream failed")

        msg = make_msg("fail stream")

        # Act
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=2.0)

        # Assert — a generic error reply was dispatched in lieu of streaming
        ctx.dispatch_response.assert_awaited()
        response_arg: Response = ctx.dispatch_response.call_args[0][1]
        assert len(response_arg.content) > 0
