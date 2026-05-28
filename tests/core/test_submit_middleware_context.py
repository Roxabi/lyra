"""Tests for resolve_context() — reply-to-resume,
MessageIndex integration (#244, #341), and session-fallthrough notification (#380)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from lyra.core.hub.middleware import PipelineContext
from lyra.core.hub.middleware.middleware_submit import SubmitToPoolMiddleware
from lyra.core.hub.middleware.path_validation import resolve_context
from lyra.core.hub.pipeline.message_pipeline import Action, PipelineResult, ResumeStatus
from tests.core.conftest import _make_hub, make_inbound_message

if TYPE_CHECKING:
    from lyra.infrastructure.stores.message_index import MessageIndex
    from lyra.infrastructure.stores.turn_store import TurnStore

# -------------------------------------------------------------------
# Stubs
# -------------------------------------------------------------------


class _FakeTurnStoreScope:
    """Minimal TurnStore stub that satisfies scope validation (#525).

    Returns *pool_id* from get_session_pool_id so Path 2 scope-check passes.
    """

    def __init__(self, pool_id: str) -> None:
        self._pool_id = pool_id

    async def get_session_pool_id(self, _session_id: str) -> str | None:
        return self._pool_id

    async def get_last_session(self, _pid: str) -> str | None:
        return None

    async def increment_resume_count(self, _sid: str) -> None:
        pass

    async def close(self) -> None:
        pass


class _StubMessageIndex:
    """Stub MessageIndex returning a canned session_id (or None)."""

    def __init__(self, mapping: dict[tuple[str, str], str] | None = None) -> None:
        self._mapping = mapping or {}
        self.resolve_calls: list[tuple[str, str]] = []

    async def resolve(self, pool_id: str, platform_msg_id: str) -> str | None:
        self.resolve_calls.append((pool_id, platform_msg_id))
        return self._mapping.get((pool_id, platform_msg_id))

    async def close(self) -> None:
        pass


class _FakeTurnStoreLastSession:
    """TurnStore stub that returns a canned last_session for a specific pool_id."""

    def __init__(self, pool_id: str, last_session: str) -> None:
        self._pool_id = pool_id
        self._last_session = last_session

    async def get_last_session(self, pid: str) -> str | None:
        return self._last_session if pid == self._pool_id else None

    async def increment_resume_count(self, _sid: str) -> None:
        pass

    async def close(self) -> None:
        pass


class _WrongPoolTurnStore:
    """TurnStore stub that returns a different pool_id for scope validation."""

    async def get_session_pool_id(self, _session_id: str) -> str | None:
        return "telegram:main:chat:99"

    async def get_last_session(self, _pid: str) -> str | None:
        return None

    async def increment_resume_count(self, _sid: str) -> None:
        pass

    async def close(self) -> None:
        pass


class _FakeTurnStoreRescue:
    """TurnStore stub for Path 3 rescue: scope ok + last_session present."""

    def __init__(self, pool_id: str) -> None:
        self._pool_id = pool_id

    async def get_session_pool_id(self, _session_id: str) -> str | None:
        return self._pool_id

    async def get_last_session(self, _pid: str) -> str | None:
        return "last-sess"

    async def increment_resume_count(self, _sid: str) -> None:
        pass

    async def close(self) -> None:
        pass


# -------------------------------------------------------------------
# Helper
# -------------------------------------------------------------------


def _make_ctx(hub) -> PipelineContext:
    return PipelineContext(hub=hub)


# -------------------------------------------------------------------
# T4.4 — reply-to-resume pipeline integration (#244)
# -------------------------------------------------------------------


class TestReplyToResumePipeline:
    """resolve_context() — reply-to-resume (#341)."""

    async def test_reply_to_resume_calls_pool_resume(self) -> None:
        """MessageIndex returns session_id — pool.resume_session is called."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "tg-msg-99"): "sess-1"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-99")
        ctx = _make_ctx(hub)

        await resolve_context(msg, pool, pool_id, ctx)

        assert resumed == ["sess-1"]

    async def test_no_resume_when_reply_to_id_none(self) -> None:
        """When msg.reply_to_id is None, MessageIndex is never called."""
        mi = _StubMessageIndex()
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool_id = "telegram:main:chat:42"
        pool = hub.get_or_create_pool(pool_id, "lyra")

        msg = make_inbound_message(scope_id="chat:42")
        assert msg.reply_to_id is None

        ctx = _make_ctx(hub)
        await resolve_context(msg, pool, pool_id, ctx)

        assert mi.resolve_calls == []

    async def test_no_resume_when_not_found(self) -> None:
        """When MessageIndex returns None, resume is not called (fallthrough)."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex()  # empty — resolve returns None
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-77")
        ctx = _make_ctx(hub)

        await resolve_context(msg, pool, pool_id, ctx)

        assert mi.resolve_calls == [(pool_id, "tg-msg-77")]
        assert resumed == []

    async def test_no_resume_when_pool_busy(self) -> None:
        """When pool.is_idle is False, resume is skipped."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "tg-msg-88"): "sess-busy"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)

        pool = hub.get_or_create_pool(pool_id, "lyra")
        _busy_task_never_completes = asyncio.Event()
        pool._current_task = asyncio.create_task(_busy_task_never_completes.wait())

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-88")
        ctx = _make_ctx(hub)

        try:
            await resolve_context(msg, pool, pool_id, ctx)
        finally:
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
            pool._current_task = None

        assert resumed == []

    async def test_reply_to_resume_works_in_group_with_user_scoped_pool(self) -> None:
        """Reply-to-resume works in groups now that pool_id is user-scoped (#356)."""
        pool_id = "telegram:main:chat:42:user:tg:user:alice"
        mi = _StubMessageIndex({(pool_id, "tg-msg-55"): "sess-alice"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)

        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42:user:tg:user:alice")
        msg = dataclasses.replace(
            _base,
            reply_to_id="tg-msg-55",
            platform_meta=dataclasses.replace(_base.platform_meta, is_group=True),
        )
        ctx = _make_ctx(hub)

        status = await resolve_context(msg, pool, pool_id, ctx)

        assert mi.resolve_calls == [(pool_id, "tg-msg-55")]
        assert resumed == ["sess-alice"]
        assert status == ResumeStatus.RESUMED

    async def test_no_resume_when_message_index_none(self) -> None:
        """When hub._message_index is None, Path 1 is skipped entirely."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        assert hub._message_index is None
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-42")
        ctx = _make_ctx(hub)

        await resolve_context(msg, pool, pool_id, ctx)

        assert resumed == []


class TestResolveContextMessageIndex:
    """Additional resolve_context tests specific to MessageIndex (#341).

    Note: cross-pool resume is impossible by design — MessageIndex.resolve
    is keyed on pool_id, so the old cross-pool guard test was removed.
    """

    async def test_resolve_via_message_index(self) -> None:
        """MessageIndex.resolve is called with pool_id + reply_to_id."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "msg-100"): "sess-abc"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="msg-100")
        ctx = _make_ctx(hub)

        await resolve_context(msg, pool, pool_id, ctx)

        assert mi.resolve_calls == [(pool_id, "msg-100")]
        assert resumed == ["sess-abc"]

    async def test_resolve_fallback_when_not_found(self) -> None:
        """When MessageIndex returns None, Path 3 fallback is tried."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex()  # empty
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="unknown-msg")
        ctx = _make_ctx(hub)

        # Should not raise — falls through to Path 3
        await resolve_context(msg, pool, pool_id, ctx)

        assert mi.resolve_calls == [(pool_id, "unknown-msg")]

    async def test_resolve_skips_when_pool_busy(self) -> None:
        """Busy pool prevents resume even when MessageIndex has a match."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "msg-busy"): "sess-x"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")
        _busy_task_never_completes = asyncio.Event()
        pool._current_task = asyncio.create_task(_busy_task_never_completes.wait())

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="msg-busy")
        ctx = _make_ctx(hub)

        try:
            await resolve_context(msg, pool, pool_id, ctx)
        finally:
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
            pool._current_task = None

        assert resumed == []


# -------------------------------------------------------------------
# T7.1 — resolve_context ResumeStatus return values (#380)
# -------------------------------------------------------------------


class TestResolveContextResumeStatus:
    """resolve_context() returns the correct ResumeStatus for each path (#380)."""

    @staticmethod
    async def _run(  # noqa: PLR0913
        pool_id: str,
        scope_id: str,
        *,
        message_index=None,
        turn_store=None,
        resume_fn=None,
        busy=False,
        reply_to_id=None,
        thread_session_id=None,
        is_group=False,
    ) -> ResumeStatus:
        hub = _make_hub()
        if message_index is not None:
            hub._message_index = cast("MessageIndex", message_index)
        if turn_store is not None:
            hub._turn_store = cast("TurnStore", turn_store)
        pool = hub.get_or_create_pool(pool_id, "lyra")
        if resume_fn is not None:
            pool._session_resume_fn = resume_fn
        if busy:
            _busy_event = asyncio.Event()
            pool._current_task = asyncio.create_task(_busy_event.wait())
        _resolved_tss = (
            pool.session_id if thread_session_id == "__auto__" else thread_session_id
        )
        _base = make_inbound_message(scope_id=scope_id)
        msg = dataclasses.replace(
            _base,
            reply_to_id=reply_to_id,
            platform_meta=dataclasses.replace(
                _base.platform_meta,
                thread_session_id=_resolved_tss,
                is_group=is_group,
            ),
        )
        ctx = _make_ctx(hub)
        try:
            status = await resolve_context(msg, pool, pool_id, ctx)
        finally:
            if busy and pool._current_task is not None:
                pool._current_task.cancel()
                try:
                    await pool._current_task
                except asyncio.CancelledError:
                    pass
                pool._current_task = None
        return status

    @pytest.mark.parametrize(
        "pool_id,scope_id,reply_to_id,thread_session_id,is_group,mi_mapping,resume_returns,turn_store,expected,expected_resume_calls",
        [
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                "tg-99",
                None,
                False,
                {("telegram:main:chat:42", "tg-99"): "sess-p1"},
                True,
                None,
                ResumeStatus.RESUMED,
                None,
                id="path1_resumed",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                None,
                "tss-1",
                False,
                None,
                True,
                _FakeTurnStoreScope("telegram:main:chat:42"),
                ResumeStatus.RESUMED,
                None,
                id="path2_accepted",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                None,
                "tss-dead",
                False,
                None,
                False,
                _FakeTurnStoreScope("telegram:main:chat:42"),
                ResumeStatus.FRESH,
                None,
                id="path2_rejected_no_path3",
            ),
            pytest.param(
                "telegram:main:chat:42:user:tg:user:alice",
                "chat:42:user:tg:user:alice",
                None,
                "tss-dead",
                True,
                None,
                False,
                _FakeTurnStoreScope("telegram:main:chat:42:user:tg:user:alice"),
                ResumeStatus.FRESH,
                None,
                id="path2_rejected_group_chat",
            ),
            pytest.param(
                "telegram:main:chat:42:user:tg:user:alice",
                "chat:42:user:tg:user:alice",
                None,
                None,
                True,
                None,
                True,
                _FakeTurnStoreLastSession(
                    "telegram:main:chat:42:user:tg:user:alice", "sess-alice-last"
                ),
                ResumeStatus.RESUMED,
                ["sess-alice-last"],
                id="path3_last_active_user_scoped",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                None,
                "tss-other",
                False,
                None,
                False,
                _WrongPoolTurnStore(),
                ResumeStatus.SKIPPED,
                None,
                id="path2_scope_mismatch",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                None,
                "tss-no-store",
                False,
                None,
                False,
                None,
                ResumeStatus.SKIPPED,
                None,
                id="path2_no_turn_store",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                None,
                None,
                False,
                None,
                False,
                None,
                ResumeStatus.SKIPPED,
                None,
                id="no_thread_session_no_turn_store",
            ),
        ],
    )
    async def test_resume_status_standard(  # noqa: PLR0913
        self,
        pool_id: str,
        scope_id: str,
        reply_to_id: str | None,
        thread_session_id: str | None,
        is_group: bool,
        mi_mapping: dict | None,
        resume_returns: bool | None,
        turn_store: object,
        expected: ResumeStatus,
        expected_resume_calls: list[str] | None,
    ) -> None:
        kwargs: dict = {}
        actual_calls: list[str] = []
        if mi_mapping is not None:
            kwargs["message_index"] = _StubMessageIndex(mi_mapping)
        if turn_store is not None:
            kwargs["turn_store"] = turn_store
        if expected_resume_calls is not None:

            async def _capturing_resume(sid: str) -> bool:
                actual_calls.append(sid)
                return True

            kwargs["resume_fn"] = _capturing_resume
        elif resume_returns is not None:

            async def _bool_resume(_sid: str) -> bool:
                return resume_returns

            kwargs["resume_fn"] = _bool_resume
        status = await self._run(
            pool_id,
            scope_id,
            reply_to_id=reply_to_id,
            thread_session_id=thread_session_id,
            is_group=is_group,
            **kwargs,
        )
        assert status == expected
        if expected_resume_calls is not None:
            assert actual_calls == expected_resume_calls

    @pytest.mark.parametrize(
        "pool_id,scope_id,thread_session_id,busy,rescue,expected",
        [
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                "tss-busy",
                True,
                False,
                ResumeStatus.SKIPPED,
                id="path2_busy_pool",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                "__auto__",
                False,
                False,
                ResumeStatus.SKIPPED,
                id="path2_already_on_session",
            ),
            pytest.param(
                "telegram:main:chat:42",
                "chat:42",
                "tss-dead",
                False,
                True,
                ResumeStatus.RESUMED,
                id="path2_rejected_path3_rescued",
            ),
        ],
    )
    async def test_resume_status_edge(  # noqa: PLR0913
        self,
        pool_id: str,
        scope_id: str,
        thread_session_id: str,
        busy: bool,
        rescue: bool,
        expected: ResumeStatus,
    ) -> None:
        kwargs: dict = {}
        resume_calls: list[str] = []
        if rescue:

            async def _rescue_fn(sid: str) -> bool:
                resume_calls.append(sid)
                return len(resume_calls) > 1

            kwargs["resume_fn"] = _rescue_fn
            kwargs["turn_store"] = _FakeTurnStoreRescue(pool_id)
        else:
            kwargs["turn_store"] = _FakeTurnStoreScope(pool_id)
        status = await self._run(
            pool_id,
            scope_id,
            thread_session_id=thread_session_id,
            busy=busy,
            **kwargs,
        )
        assert status == expected
        if rescue:
            assert len(resume_calls) == 2


# -------------------------------------------------------------------
# T7.3 — Path 3 dead-backend guard (#415)
# -------------------------------------------------------------------


class TestPath3DeadBackendGuard:
    """Path 3: last_sid == pool.session_id + dead backend falls through (#415)."""

    async def test_path3_falls_through_when_backend_dead_and_session_matches(
        self,
    ) -> None:
        """last_sid == pool.session_id + is_backend_alive() False → NOT SKIPPED.

        The dead-backend guard must not return SKIPPED immediately when the
        pool's current session_id matches the stored last session but the
        backend is dead.  With path2_attempted=True (a thread_session_id was
        present but rejected), the final result must be FRESH — not SKIPPED.
        """
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        # Path 2: thread_session_id present but rejected (returns False).
        async def _rejected_resume(_sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume

        # Override is_backend_alive to simulate a dead backend
        agent = hub.agent_registry.get("lyra")
        assert agent is not None
        object.__setattr__(agent, "is_backend_alive", lambda _pool_id: False)

        class _FakeTurnStore:
            async def get_session_pool_id(self, _session_id: str) -> str | None:
                return pool_id  # scope validation passes (#525)

            async def get_last_session(self, _pid: str) -> str | None:
                return pool.session_id  # matches pool.session_id exactly

            async def increment_resume_count(self, _sid: str) -> None:
                pass

            async def close(self) -> None:
                pass

        hub._turn_store = cast("TurnStore", _FakeTurnStore())

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta=dataclasses.replace(
                _base.platform_meta, thread_session_id="tss-dead"
            ),
        )
        ctx = _make_ctx(hub)

        status = await resolve_context(msg, pool, pool_id, ctx)

        # If the dead-backend guard had returned SKIPPED at the inner check,
        # the result would be SKIPPED.  Falling through gives FRESH because
        # path2 was attempted but rejected.
        assert status == ResumeStatus.FRESH

    async def test_path3_skips_when_backend_alive_and_session_matches(
        self,
    ) -> None:
        """last_sid == pool.session_id + is_backend_alive() True → SKIPPED.

        When the backend is alive and the session_id already matches, the guard
        correctly returns SKIPPED (pool is already on the right session).
        """
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        # _NullAgent inherits is_backend_alive → True (default)
        agent = hub.agent_registry.get("lyra")
        assert agent is not None
        assert agent.is_backend_alive(pool_id) is True

        class _FakeTurnStore:
            async def get_last_session(self, _pid: str) -> str | None:
                return pool.session_id  # matches pool.session_id exactly

            async def close(self) -> None:
                pass

        hub._turn_store = cast("TurnStore", _FakeTurnStore())

        msg = make_inbound_message(scope_id="chat:42")
        ctx = _make_ctx(hub)

        status = await resolve_context(msg, pool, pool_id, ctx)

        assert status == ResumeStatus.SKIPPED


# -------------------------------------------------------------------
# T7.2 — _submit_to_pool notification on FRESH (#380) via resolve_context
# -------------------------------------------------------------------


class TestNotifySessionFallthrough:
    """SubmitToPoolMiddleware sends a pre-response notice iff status is FRESH (#380)."""

    async def test_notify_called_when_fresh(self) -> None:
        """FRESH status triggers try_notify_user before pool submit."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _rejected_resume(_sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume

        # Wire fake TurnStore so scope validation passes (#525).
        hub._turn_store = cast("TurnStore", _FakeTurnStoreScope(pool_id))

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta=dataclasses.replace(
                _base.platform_meta, thread_session_id="tss-dead"
            ),
        )

        from lyra.core.hub.hub_protocol import RoutingKey
        from lyra.core.messaging.message import Platform

        key = RoutingKey(Platform("telegram"), "main", "chat:42")
        ctx = _make_ctx(hub)
        ctx.pool = pool
        ctx.key = key
        mw = SubmitToPoolMiddleware()

        notify_calls: list[tuple] = []

        async def _fake_notify(platform: str, _a, _o, text: str, **_kw) -> None:
            notify_calls.append((platform, text))

        _patch = "lyra.core.hub.outbound.outbound_errors.try_notify_user"

        async def _noop_next(_m, _c):
            return PipelineResult(action=Action.SUBMIT_TO_POOL, pool=pool)

        with patch(_patch, side_effect=_fake_notify):
            result = await mw(msg, ctx, _noop_next)

        assert result.action == Action.SUBMIT_TO_POOL
        assert len(notify_calls) == 1
        platform_called, text_called = notify_calls[0]
        assert platform_called == "telegram"
        assert "starting fresh" in text_called

    async def test_no_notify_when_resumed(self) -> None:
        """RESUMED status — no notification sent."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _accepted_resume(_sid: str) -> bool:
            return True

        pool._session_resume_fn = _accepted_resume

        # Wire TurnStore so path 2 scope check passes.
        hub._turn_store = cast("TurnStore", _FakeTurnStoreScope(pool_id))

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta=dataclasses.replace(
                _base.platform_meta, thread_session_id="tss-live"
            ),
        )

        from lyra.core.hub.hub_protocol import RoutingKey
        from lyra.core.messaging.message import Platform

        key = RoutingKey(Platform("telegram"), "main", "chat:42")
        ctx = _make_ctx(hub)
        ctx.pool = pool
        ctx.key = key
        mw = SubmitToPoolMiddleware()

        notify_calls: list = []

        async def _fake_notify(*_args, **_kw) -> None:
            notify_calls.append(_args)

        _patch = "lyra.core.hub.outbound.outbound_errors.try_notify_user"

        async def _noop_next(_m, _c):
            return PipelineResult(action=Action.SUBMIT_TO_POOL, pool=pool)

        with patch(_patch, side_effect=_fake_notify):
            result = await mw(msg, ctx, _noop_next)

        assert result.action == Action.SUBMIT_TO_POOL
        assert notify_calls == []

    async def test_no_notify_when_skipped(self) -> None:
        """SKIPPED status (no thread_session_id) — no notification sent."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        msg = make_inbound_message(scope_id="chat:42")

        from lyra.core.hub.hub_protocol import RoutingKey
        from lyra.core.messaging.message import Platform

        key = RoutingKey(Platform("telegram"), "main", "chat:42")
        ctx = _make_ctx(hub)
        ctx.pool = pool
        ctx.key = key
        mw = SubmitToPoolMiddleware()

        notify_calls: list = []

        async def _fake_notify(*_args, **_kw) -> None:
            notify_calls.append(_args)

        _patch = "lyra.core.hub.outbound.outbound_errors.try_notify_user"

        async def _noop_next(_m, _c):
            return PipelineResult(action=Action.SUBMIT_TO_POOL, pool=pool)

        with patch(_patch, side_effect=_fake_notify):
            result = await mw(msg, ctx, _noop_next)

        assert result.action == Action.SUBMIT_TO_POOL
        assert notify_calls == []
