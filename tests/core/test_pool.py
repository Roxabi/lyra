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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.message import (
    InboundMessage,
    Response,
)
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel

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

    async def reset_backend(self, pool_id: str) -> None:
        pass

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        await asyncio.sleep(10)  # never finishes in test
        return Response(content="done")


class FastAgent:
    """Agent that echoes the message text immediately."""

    name = "test_agent"

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        return Response(content=f"echo: {msg.text}")


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
    async def test_pool_timeout_logs_dead_backend_error(
        self, fast_pool: Pool, ctx_mock: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Timeout with dead backend logs ERROR distinguishing from normal timeout."""
        # Arrange — patch is_backend_alive to return False (dead backend)
        import logging

        agent = SlowAgent()
        agent.is_backend_alive = lambda pool_id: False  # type: ignore[method-assign]
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.core.pool_processor"):
            fast_pool.submit(msg)
            await _drain(fast_pool, timeout=2.0)

        # Assert — pool logged ERROR about dead backend process
        assert any("backend process died" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_pool_timeout_does_not_log_dead_backend_when_alive(
        self, fast_pool: Pool, ctx_mock: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Normal timeout (live backend) does NOT log the dead-backend ERROR."""
        import logging

        agent = SlowAgent()  # is_backend_alive returns True by default
        ctx_mock._agents["test_agent"] = agent
        msg = make_msg()

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool_processor"):
            fast_pool.submit(msg)
            await _drain(fast_pool, timeout=2.0)

        # Assert — no dead-backend error logged for a live backend
        assert not any("backend process died" in r.message for r in caplog.records)

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


class TestPoolSwitchWorkspace:
    @pytest.mark.asyncio
    async def test_switch_workspace_calls_fn_with_cwd(self, tmp_path: Path) -> None:
        """switch_workspace calls _switch_workspace_fn with the given cwd."""
        ctx = MagicMock()
        ctx.get_message.return_value = None
        pool = Pool("pool-ws", "agent", ctx)

        called_with: list[Path] = []

        async def fake_switch(cwd: Path) -> None:
            called_with.append(cwd)

        pool._switch_workspace_fn = fake_switch
        await pool.switch_workspace(tmp_path)

        assert called_with == [tmp_path]
        assert list(pool.sdk_history) == []
        assert pool.history == []

    @pytest.mark.asyncio
    async def test_switch_workspace_noop_when_fn_is_none(self, tmp_path: Path) -> None:
        """switch_workspace is a no-op when _switch_workspace_fn is None (SDK)."""
        ctx = MagicMock()
        ctx.get_message.return_value = None
        pool = Pool("pool-sdk", "agent", ctx)
        pool.history = [MagicMock()]
        pool.sdk_history.append({"role": "user", "content": "hello"})

        # _switch_workspace_fn is None by default
        await pool.switch_workspace(tmp_path)

        # History must NOT be cleared (no-op for SDK backends — B7 fix)
        assert len(pool.history) == 1
        assert len(pool.sdk_history) == 1


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
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> collections.abc.AsyncIterator[str]:
        async def _gen() -> collections.abc.AsyncIterator[str]:
            yield "hello "
            yield "world"

        return _gen()


class FailingStreamingAgent:
    """Test double: returns a generator that raises mid-stream."""

    name = "test_agent"

    async def process(  # type: ignore[override]
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> collections.abc.AsyncIterator[str]:
        async def _gen() -> collections.abc.AsyncIterator[str]:
            yield "partial"
            raise RuntimeError("stream error")

        return _gen()


# ---------------------------------------------------------------------------
# T3.4 — Pool.resume_session() — reply-to-resume (#244)
# ---------------------------------------------------------------------------


class TestPoolResumeSession:
    """Pool.resume_session() delegates to _session_resume_fn (T3.4, SC-4)."""

    @pytest.mark.asyncio
    async def test_resume_session_calls_fn(self) -> None:
        """resume_session() calls _session_resume_fn with the given session_id."""
        # Arrange
        ctx = _make_ctx_mock()
        pool = Pool("p1", "agent", ctx=ctx)
        called_with: list[str] = []

        async def _fake_resume(sid: str) -> None:
            called_with.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        # Act
        await pool.resume_session("sess-xyz")  # type: ignore[attr-defined]

        # Assert
        assert called_with == ["sess-xyz"]

    @pytest.mark.asyncio
    async def test_resume_session_noop_when_fn_none(self) -> None:
        """resume_session() is a no-op when _session_resume_fn is None (SDK pools)."""
        # Arrange
        ctx = _make_ctx_mock()
        pool = Pool("p1", "agent", ctx=ctx)
        # _session_resume_fn is None by default

        # Act / Assert — must not raise
        await pool.resume_session("sess-xyz")  # type: ignore[attr-defined]


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


class TestPoolUnknownAgentDrain:
    """When get_agent returns None, inbox is drained gracefully (no crash)."""

    @pytest.mark.asyncio
    async def test_process_loop_unknown_agent_drains_inbox(
        self, ctx_mock: MagicMock
    ) -> None:
        """When the agent is not found, queued messages are drained without dispatch."""
        ctx_mock.get_agent.return_value = None  # no agent registered

        pool = Pool(
            pool_id="test:main:chat:noagent",
            agent_name="test_agent",
            ctx=ctx_mock,
            turn_timeout=60.0,
            debounce_ms=0,
        )

        msg1 = make_msg("msg1")
        msg2 = make_msg("msg2")
        pool._inbox.put_nowait(msg1)
        pool._inbox.put_nowait(msg2)

        # Start the processing loop directly and let it run
        task = asyncio.create_task(pool._processor.process_loop())
        await asyncio.sleep(0.1)

        # dispatch_response should never be called — no agent found
        ctx_mock.dispatch_response.assert_not_called()
        # Inbox should be empty (drained)
        assert pool._inbox.empty()
        assert task.done()


class TestPoolCancelInboxRace:
    """cancel-in-flight: agent_task and inbox_waiter complete simultaneously."""

    @pytest.mark.asyncio
    async def test_process_with_cancel_inbox_race(self, ctx_mock: MagicMock) -> None:
        """Two messages submitted rapidly — both are processed (second via re-queue)."""
        results: list[str] = []

        class RecordingAgent:
            name = "test_agent"

            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate=None,
            ) -> Response:
                results.append(msg.text)
                return Response(content="ok")

        agent = RecordingAgent()
        ctx_mock._agents["test_agent"] = agent
        pool = Pool(
            pool_id="test:main:chat:race",
            agent_name="test_agent",
            ctx=ctx_mock,
            turn_timeout=60.0,
            debounce_ms=0,
        )

        msg1 = make_msg("first")
        msg2 = make_msg("second")
        pool.submit(msg1)
        pool.submit(msg2)

        await _drain(pool, timeout=3.0)

        # Both messages should have been seen (may be merged into one turn by debouncer)
        combined = "\n".join(results)
        assert "first" in combined
        assert "second" in combined


# S1 — Pool identity / session fields (issue #83)
#
# RED phase: these tests FAIL until Pool gains session_id, user_id, medium,
# message_count, session_start, _system_prompt, _turn_logger and append().
# ---------------------------------------------------------------------------


class TestPoolIdentity:
    """Pool must carry per-session identity metadata (S1)."""

    def test_pool_has_session_id(self, pool: Pool) -> None:
        """Pool.session_id must be a UUID4 string (36 chars with dashes)."""
        assert isinstance(pool.session_id, str) and len(pool.session_id) == 36

    def test_pool_identity_defaults(self, pool: Pool) -> None:
        """user_id, medium and message_count default to empty/zero on creation."""
        assert pool.user_id == "" and pool.medium == "" and pool.message_count == 0

    def test_pool_session_start_is_utc(self, pool: Pool) -> None:
        """session_start must be timezone-aware (UTC)."""

        assert pool.session_start.tzinfo is not None

    def test_pool_system_prompt_default(self, pool: Pool) -> None:
        """_system_prompt defaults to empty string before any message."""
        assert pool._system_prompt == ""

    def test_pool_turn_logger_default(self, pool: Pool) -> None:
        """_turn_logger defaults to None — no logger wired by default."""
        assert pool._turn_logger is None


def _make_inbound(
    user_id: str = "u1", platform: str = "telegram", text: str = "hello"
) -> InboundMessage:
    """Build a minimal InboundMessage for S1 tests."""
    return InboundMessage(
        id="s1-msg",
        platform=platform,
        bot_id="main",
        scope_id="chat:1",
        user_id=user_id,
        user_name="Test",
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


class TestPoolAppend:
    """Pool.append() must update session identity fields (S1)."""

    def test_append_promotes_user_id_once(self, pool: Pool) -> None:
        """First append sets user_id; subsequent appends with different user_id
        must NOT overwrite it (first-writer-wins semantics)."""
        msg1 = _make_inbound(user_id="u1", platform="telegram")
        pool.append(msg1)
        assert pool.user_id == "u1" and pool.message_count == 1

        msg2 = _make_inbound(user_id="u2", platform="discord")
        pool.append(msg2)
        assert pool.user_id == "u1"  # not overwritten

    def test_append_increments_message_count(self, pool: Pool) -> None:
        """message_count increments once per append call."""
        pool.append(_make_inbound())
        pool.append(_make_inbound())
        assert pool.message_count == 2

    def test_append_sets_medium_from_platform(self, pool: Pool) -> None:
        """First append captures medium (platform string) from the message."""
        pool.append(_make_inbound(platform="telegram"))
        assert pool.medium == "telegram"

    def test_append_calls_turn_logger_no_error(self, pool: Pool) -> None:
        """When _turn_logger is set, append() must not raise any error."""
        calls: list = []

        async def fake_logger(sid: str, m: object) -> None:
            calls.append((sid, m))

        pool._turn_logger = fake_logger
        pool.append(_make_inbound())
        # turn_logger may be called via create_task; at minimum no sync error
        assert pool.message_count == 1


class TestPoolSnapshot:
    """Pool.snapshot() must return a SessionSnapshot (S1)."""

    def test_snapshot_returns_session_snapshot(self, pool: Pool) -> None:
        """snapshot() returns a SessionSnapshot with correct identity fields."""
        from lyra.core.memory import SessionSnapshot

        pool.append(_make_inbound(user_id="u1", platform="telegram"))
        snap = pool.snapshot("lyra")
        assert isinstance(snap, SessionSnapshot)
        assert snap.session_id == pool.session_id
        assert snap.user_id == "u1"
        assert snap.medium == "telegram"
        assert snap.message_count == 1
        assert snap.session_end >= snap.session_start
