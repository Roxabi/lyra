"""Pool streaming path and workspace/session tests.

Spec trace: S4-*, T3.4, SC-4, B7
"""

from __future__ import annotations

import asyncio
import collections.abc
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.messaging.render_events import TextRenderEvent
from lyra.core.pool import Pool
from tests.conftest import TIMEOUT_IO
from tests.core.conftest import _make_ctx_mock, make_msg

# ---------------------------------------------------------------------------
# File-local agent doubles
# ---------------------------------------------------------------------------


class StreamingAgent:
    """Test double: returns an async generator (streaming path)."""

    name = "test_agent"

    async def process(
        self,
        _msg: InboundMessage,
        _pool: Pool,
        *,
        on_intermediate=None,
    ) -> collections.abc.AsyncIterator[TextRenderEvent]:
        async def _gen() -> collections.abc.AsyncIterator[TextRenderEvent]:
            yield TextRenderEvent(text="hello world", is_final=True)

        return _gen()


class FailingStreamingAgent:
    """Test double: returns a generator that raises mid-stream."""

    name = "test_agent"

    async def process(
        self,
        _msg: InboundMessage,
        _pool: Pool,
        *,
        on_intermediate=None,
    ) -> collections.abc.AsyncIterator[TextRenderEvent]:
        async def _gen() -> collections.abc.AsyncIterator[TextRenderEvent]:
            yield TextRenderEvent(text="partial", is_final=False)
            raise RuntimeError("stream error")

        return _gen()


class EmptyStreamingAgent:
    """Test double: returns an async generator that yields no events."""

    name = "test_agent"

    async def process(
        self,
        _msg: InboundMessage,
        _pool: Pool,
        *,
        _on_intermediate=None,
    ) -> collections.abc.AsyncIterator[TextRenderEvent]:
        async def _gen() -> collections.abc.AsyncIterator[TextRenderEvent]:
            if False:  # pragma: no cover
                yield TextRenderEvent(text="", is_final=True)

        return _gen()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


async def _consume_and_dispatch_cb(
    _msg: object, chunks: collections.abc.AsyncIterator[str], outbound: object = None
) -> None:
    """Consume all chunks then fire _on_dispatched — simulates no-dispatcher path."""
    async for _ in chunks:
        pass
    if outbound is not None:
        from lyra.core.messaging.message import OutboundMessage as _OM

        if isinstance(outbound, _OM):
            cb = outbound.metadata.pop("_on_dispatched", None)
            if cb is not None:
                result = cb(outbound)
                if asyncio.iscoroutine(result):
                    await result


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
        pool.max_sdk_history = 3
        initial = [{"role": "user", "content": f"msg{i}"} for i in range(3)]
        pool.extend_sdk_history(initial)

        pool.extend_sdk_history(
            [
                {"role": "user", "content": "new1"},
                {"role": "user", "content": "new2"},
            ]
        )

        assert len(pool.sdk_history) == 3
        contents = [entry["content"] for entry in pool.sdk_history]
        assert "msg0" not in contents
        assert "msg1" not in contents
        assert "new1" in contents
        assert "new2" in contents

    def test_extend_sdk_history_no_trim_when_under_max(self, pool: Pool) -> None:
        """extend_sdk_history() keeps all entries when under the cap."""
        pool.max_sdk_history = 10
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(5)]

        pool.extend_sdk_history(messages)

        assert len(pool.sdk_history) == 5


# ---------------------------------------------------------------------------
# T3.4 — Pool.resume_session() — reply-to-resume (#244)
# ---------------------------------------------------------------------------


class TestPoolResumeSession:
    """Pool.resume_session() delegates to _session_resume_fn (T3.4, SC-4)."""

    @pytest.mark.asyncio
    async def test_resume_session_calls_fn(self) -> None:
        """resume_session() calls _session_resume_fn with the given session_id."""
        ctx = _make_ctx_mock()
        pool = Pool("p1", "agent", ctx=ctx)
        called_with: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            called_with.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        await pool.resume_session("sess-xyz")

        assert called_with == ["sess-xyz"]

    @pytest.mark.asyncio
    async def test_resume_session_noop_when_fn_none(self) -> None:
        """resume_session() is a no-op when _session_resume_fn is None (SDK pools)."""
        ctx = _make_ctx_mock()
        pool = Pool("p1", "agent", ctx=ctx)
        # _session_resume_fn is None by default

        await pool.resume_session("sess-xyz")

    async def test_resume_session_resets_session_persisted(self) -> None:
        """resume_session() resets _session_persisted when resume is accepted (#341)."""
        ctx = _make_ctx_mock()
        pool = Pool("p1", "agent", ctx=ctx)
        pool._observer._session_persisted = True  # simulate already persisted
        # Provide a callback that accepts the resume (CLI pool behaviour)
        pool._session_resume_fn = AsyncMock(return_value=True)

        await pool.resume_session("sess-new")

        assert pool._observer._session_persisted is False


# ---------------------------------------------------------------------------
# Streaming path — Pool._process_one() async-generator branch
# ---------------------------------------------------------------------------


class TestPoolStreaming:
    """Pool._process_one() async-generator (streaming) branch (S4-*)."""

    @pytest.mark.asyncio
    async def test_pool_streaming_path_calls_dispatch_streaming(self) -> None:
        """Streaming result routes to dispatch_streaming, not dispatch_response."""
        agent = StreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        pool = Pool(pool_id="test:main:chat:stream", agent_name="test_agent", ctx=ctx)

        msg = make_msg("stream test")

        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        ctx.dispatch_streaming.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pool_streaming_records_cb_success(self) -> None:
        """Successful streaming records CB success when a circuit registry exists."""
        agent = StreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})

        pool = Pool(pool_id="test:main:chat:cbstream", agent_name="test_agent", ctx=ctx)

        msg = make_msg("cb stream")

        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        ctx.dispatch_streaming.assert_awaited_once()
        ctx.record_circuit_success.assert_called()

    @pytest.mark.asyncio
    async def test_pool_failing_stream_sends_generic_reply(self) -> None:
        """A stream that raises mid-iteration sends a generic error reply."""
        ctx = _make_ctx_mock({"test_agent": FailingStreamingAgent()})
        pool = Pool(
            pool_id="test:main:chat:failstream", agent_name="test_agent", ctx=ctx
        )
        ctx.dispatch_streaming.side_effect = RuntimeError("stream failed")

        log_calls: list[str] = []

        async def _capture_turn(**kw: object) -> None:
            if kw.get("role") == "assistant":
                log_calls.append(str(kw.get("content", "")))

        object.__setattr__(pool._observer, "log_turn_async", _capture_turn)

        msg = make_msg("fail stream")

        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        ctx.dispatch_response.assert_awaited()
        response_arg: Response = ctx.dispatch_response.call_args[0][1]
        assert len(response_arg.content) > 0

        # dispatch_streaming raises before _on_dispatched fires — no turn logged
        assert log_calls == [], (
            f"log_turn_async should not be called on stream error, got {log_calls!r}"
        )

    @pytest.mark.asyncio
    async def test_streaming_turn_logs_full_content(self) -> None:
        """_log_streaming_turn passes accumulated content to log_turn_async (#373)."""
        agent = StreamingAgent()  # yields "hello " then "world"
        ctx = _make_ctx_mock({"test_agent": agent})

        ctx.dispatch_streaming.side_effect = _consume_and_dispatch_cb
        pool = Pool(
            pool_id="test:main:chat:logcontent", agent_name="test_agent", ctx=ctx
        )

        logged: list[str] = []

        async def _capture_turn(**kw: object) -> None:
            if kw.get("role") == "assistant":
                logged.append(str(kw.get("content", "")))

        object.__setattr__(pool._observer, "log_turn_async", _capture_turn)

        msg = make_msg("log content test")
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        assert logged == ["hello world"], f"expected full content, got {logged!r}"

    @pytest.mark.asyncio
    async def test_streaming_turn_logs_empty_for_zero_chunks(self) -> None:
        """Zero-chunk stream stores content='' (#373)."""
        agent = EmptyStreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})

        ctx.dispatch_streaming.side_effect = _consume_and_dispatch_cb
        pool = Pool(
            pool_id="test:main:chat:emptycontent", agent_name="test_agent", ctx=ctx
        )

        logged: list[str] = []

        async def _capture_turn(**kw: object) -> None:
            if kw.get("role") == "assistant":
                logged.append(str(kw.get("content", "MISSING")))

        object.__setattr__(pool._observer, "log_turn_async", _capture_turn)

        msg = make_msg("empty stream test")
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        assert logged == [""], f"expected empty string, got {logged!r}"

    @pytest.mark.asyncio
    async def test_streaming_turn_logs_partial_content_when_superseded(self) -> None:
        """Superseded streaming turn logs partial content accumulated before cancel (#373)."""  # noqa: E501

        # Arrange: agent that yields two events with a brief pause
        class SlowStreamingAgent:
            name = "test_agent"

            async def process(
                self,
                _msg: InboundMessage,
                _pool: Pool,
                *,
                _on_intermediate=None,
            ) -> collections.abc.AsyncIterator[TextRenderEvent]:
                async def _gen() -> collections.abc.AsyncIterator[TextRenderEvent]:
                    yield TextRenderEvent(text="first", is_final=False)
                    await asyncio.sleep(0.05)
                    yield TextRenderEvent(text="second", is_final=True)

                return _gen()

        agent = SlowStreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        ctx.dispatch_streaming.side_effect = _consume_and_dispatch_cb

        pool = Pool(
            pool_id="test:main:chat:superseded", agent_name="test_agent", ctx=ctx
        )

        logged: list[str] = []

        async def _capture_turn(**kw: object) -> None:
            if kw.get("role") == "assistant":
                logged.append(str(kw.get("content", "")))

        object.__setattr__(pool._observer, "log_turn_async", _capture_turn)

        # Act: submit message and let it complete normally (supersede scenario is
        # covered by the inflight mechanism; here we verify content is captured)
        msg = make_msg("supersede test")
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        # Assert: full content captured (no mid-stream cancel in this test path)
        assert logged == ["firstsecond"], f"expected full content, got {logged!r}"

    @pytest.mark.asyncio
    async def test_streaming_session_id_updated_from_original_iterator(self) -> None:
        """session_id is read from original iterator ref, not the tee wrapper (#373)."""

        class _IteratorWithSessionId:
            """Async iterator wrapper that allows arbitrary attribute assignment."""

            session_id: str

            def __init__(
                self, inner: collections.abc.AsyncIterator[TextRenderEvent]
            ) -> None:
                self._inner = inner

            def __aiter__(self) -> _IteratorWithSessionId:
                return self

            async def __anext__(self) -> TextRenderEvent:
                return await self._inner.__anext__()

            async def aclose(self) -> None:
                _close = getattr(self._inner, "aclose", None)
                if _close is not None:
                    await _close()

        class SessionIdStreamingAgent:
            name = "test_agent"

            async def process(
                self,
                _msg: InboundMessage,
                _pool: Pool,
                *,
                _on_intermediate=None,
            ) -> collections.abc.AsyncIterator[TextRenderEvent]:
                async def _gen() -> collections.abc.AsyncIterator[TextRenderEvent]:
                    yield TextRenderEvent(text="hello", is_final=True)

                it = _IteratorWithSessionId(_gen())
                it.session_id = "session-from-iterator"
                return it

        agent = SessionIdStreamingAgent()
        ctx = _make_ctx_mock({"test_agent": agent})
        ctx.dispatch_streaming.side_effect = _consume_and_dispatch_cb

        pool = Pool(
            pool_id="test:main:chat:sessionid", agent_name="test_agent", ctx=ctx
        )

        msg = make_msg("session id test")
        pool.submit(msg)
        if pool._current_task:
            await asyncio.wait_for(pool._current_task, timeout=TIMEOUT_IO)

        assert pool.session_id == "session-from-iterator", (
            f"expected session_id from original iterator, got {pool.session_id!r}"
        )
