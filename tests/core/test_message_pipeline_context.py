"""Tests for MessagePipeline._resolve_context() — reply-to-resume,
MessageIndex integration (#244, #341), and session-fallthrough notification (#380)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from lyra.core.hub.message_pipeline import Action, MessagePipeline, ResumeStatus
from tests.core.conftest import _make_hub, make_inbound_message

if TYPE_CHECKING:
    from lyra.core.stores.message_index import MessageIndex
    from lyra.core.stores.turn_store import TurnStore

# -------------------------------------------------------------------
# Stub
# -------------------------------------------------------------------


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


# -------------------------------------------------------------------
# T4.4 — reply-to-resume pipeline integration (#244)
# -------------------------------------------------------------------


class TestReplyToResumePipeline:
    """MessagePipeline._resolve_context() reply-to-resume via MessageIndex (#341)."""

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
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)

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

        pipeline = MessagePipeline(hub)
        await pipeline._resolve_context(msg, pool, pool_id)

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
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)

        assert mi.resolve_calls == [(pool_id, "tg-msg-77")]
        assert resumed == []

    async def test_no_resume_when_pool_busy(self) -> None:
        """When pool.is_idle is False, resume is skipped."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "tg-msg-88"): "sess-busy"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)

        pool = hub.get_or_create_pool(pool_id, "lyra")
        pool._current_task = asyncio.create_task(asyncio.sleep(10))

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-88")
        pipeline = MessagePipeline(hub)

        try:
            await pipeline._resolve_context(msg, pool, pool_id)
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
            platform_meta={**_base.platform_meta, "is_group": True},
        )
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

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
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)

        assert resumed == []


class TestResolveContextMessageIndex:
    """Additional _resolve_context tests specific to MessageIndex (#341).

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
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)

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
        pipeline = MessagePipeline(hub)

        # Should not raise — falls through to Path 3
        await pipeline._resolve_context(msg, pool, pool_id)

        assert mi.resolve_calls == [(pool_id, "unknown-msg")]

    async def test_resolve_skips_when_pool_busy(self) -> None:
        """Busy pool prevents resume even when MessageIndex has a match."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "msg-busy"): "sess-x"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")
        pool._current_task = asyncio.create_task(asyncio.sleep(10))

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="msg-busy")
        pipeline = MessagePipeline(hub)

        try:
            await pipeline._resolve_context(msg, pool, pool_id)
        finally:
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
            pool._current_task = None

        assert resumed == []


# -------------------------------------------------------------------
# T7.1 — _resolve_context ResumeStatus return values (#380)
# -------------------------------------------------------------------


class TestResolveContextResumeStatus:
    """_resolve_context() returns the correct ResumeStatus for each path (#380)."""

    async def test_path1_resumed_returns_resumed(self) -> None:
        """Path 1 successful resume → RESUMED."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "tg-99"): "sess-p1"})
        hub = _make_hub()
        hub._message_index = cast("MessageIndex", mi)
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-99")
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.RESUMED

    async def test_path2_accepted_returns_resumed(self) -> None:
        """Path 2 accepted → RESUMED."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-1"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.RESUMED

    async def test_path2_rejected_no_path3_returns_fresh(self) -> None:
        """Path 2 rejected, no TurnStore → FRESH (user must be notified)."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return False  # session pruned / invalid

        pool._session_resume_fn = _fake_resume

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.FRESH

    async def test_path2_rejected_path3_rescued_returns_resumed(self) -> None:
        """Path 2 rejected, Path 3 rescues → RESUMED (no notification)."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resume_calls: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resume_calls.append(sid)
            # First call (path 2) fails; second call (path 3) succeeds.
            return len(resume_calls) > 1

        pool._session_resume_fn = _fake_resume

        class _FakeTurnStore:
            async def get_last_session(self, pid: str) -> str | None:
                return "last-sess"

            async def increment_resume_count(self, sid: str) -> None:
                pass

            async def close(self) -> None:
                pass

        hub._turn_store = cast("TurnStore", _FakeTurnStore())

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.RESUMED
        assert len(resume_calls) == 2  # path 2 + path 3

    async def test_no_thread_session_no_turn_store_returns_skipped(self) -> None:
        """No thread_session_id and no TurnStore → SKIPPED (first-use, silent)."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        msg = make_inbound_message(scope_id="chat:42")
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.SKIPPED

    async def test_path2_busy_pool_returns_skipped(self) -> None:
        """thread_session_id present but pool busy → SKIPPED (not a surprise)."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")
        pool._current_task = asyncio.create_task(asyncio.sleep(10))

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-busy"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        try:
            status = await pipeline._resolve_context(msg, pool, pool_id)
        finally:
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
            pool._current_task = None

        assert status == ResumeStatus.SKIPPED

    async def test_path2_rejected_group_chat_returns_fresh(self) -> None:
        """Path 2 rejected in a group chat → FRESH (no is_group guard since #356).

        With user-scoped pool_ids, group chats no longer need is_group guards.
        Path 2 rejection falls through to Path 3, and without a TurnStore
        this returns FRESH (user should be notified of fresh start).
        """
        pool_id = "telegram:main:chat:42:user:tg:user:alice"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _rejected_resume(sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume

        _base = make_inbound_message(scope_id="chat:42:user:tg:user:alice")
        # is_group in platform_meta no longer affects the pipeline path since #356
        # — included here only to document that it is now inert.
        _meta = {
            **_base.platform_meta,
            "thread_session_id": "tss-dead",
            "is_group": True,
        }
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.FRESH

    async def test_path3_last_active_works_with_user_scoped_pool(self) -> None:
        """Path 3: last-active-session works with user-scoped pool_id (#356)."""
        pool_id = "telegram:main:chat:42:user:tg:user:alice"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> bool:
            resumed.append(sid)
            return True

        pool._session_resume_fn = _fake_resume

        class _FakeTurnStore:
            async def get_last_session(self, pid: str) -> str | None:
                return "sess-alice-last" if pid == pool_id else None

            async def increment_resume_count(self, sid: str) -> None:
                pass

            async def close(self) -> None:
                pass

        hub._turn_store = cast("TurnStore", _FakeTurnStore())

        _base = make_inbound_message(scope_id="chat:42:user:tg:user:alice")
        _meta = {**_base.platform_meta, "is_group": True}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.RESUMED
        assert resumed == ["sess-alice-last"]


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
        async def _rejected_resume(sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume

        # Override is_backend_alive to simulate a dead backend
        agent = hub.agent_registry.get("lyra")
        assert agent is not None
        object.__setattr__(agent, "is_backend_alive", lambda _pool_id: False)

        class _FakeTurnStore:
            async def get_last_session(self, pid: str) -> str | None:
                return pool.session_id  # matches pool.session_id exactly

            async def close(self) -> None:
                pass

        hub._turn_store = cast("TurnStore", _FakeTurnStore())

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

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
            async def get_last_session(self, pid: str) -> str | None:
                return pool.session_id  # matches pool.session_id exactly

            async def close(self) -> None:
                pass

        hub._turn_store = cast("TurnStore", _FakeTurnStore())

        msg = make_inbound_message(scope_id="chat:42")
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)

        assert status == ResumeStatus.SKIPPED


# -------------------------------------------------------------------
# T7.2 — _submit_to_pool notification on FRESH (#380)
# -------------------------------------------------------------------


class TestNotifySessionFallthrough:
    """_submit_to_pool sends a pre-response notice iff status is FRESH (#380)."""

    async def test_notify_called_when_fresh(self) -> None:
        """FRESH status triggers try_notify_user before pool submit."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _rejected_resume(sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        notify_calls: list[tuple] = []

        async def _fake_notify(platform: str, _a, _o, text: str, **_kw) -> None:
            notify_calls.append((platform, text))

        _patch = "lyra.core.hub.outbound_errors.try_notify_user"
        with patch(_patch, side_effect=_fake_notify):
            from lyra.core.hub import RoutingKey
            from lyra.core.message import Platform

            key = RoutingKey(Platform("telegram"), "main", "chat:42")
            result = await pipeline._submit_to_pool(msg, pool, key)

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

        async def _accepted_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _accepted_resume

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-live"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        notify_calls: list = []

        async def _fake_notify(*_args, **_kw) -> None:
            notify_calls.append(_args)

        _patch = "lyra.core.hub.outbound_errors.try_notify_user"
        with patch(_patch, side_effect=_fake_notify):
            from lyra.core.hub import RoutingKey
            from lyra.core.message import Platform

            key = RoutingKey(Platform("telegram"), "main", "chat:42")
            result = await pipeline._submit_to_pool(msg, pool, key)

        assert result.action == Action.SUBMIT_TO_POOL
        assert notify_calls == []

    async def test_no_notify_when_skipped(self) -> None:
        """SKIPPED status (no thread_session_id) — no notification sent."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        msg = make_inbound_message(scope_id="chat:42")
        pipeline = MessagePipeline(hub)

        notify_calls: list = []

        async def _fake_notify(*_args, **_kw) -> None:
            notify_calls.append(_args)

        _patch = "lyra.core.hub.outbound_errors.try_notify_user"
        with patch(_patch, side_effect=_fake_notify):
            from lyra.core.hub import RoutingKey
            from lyra.core.message import Platform

            key = RoutingKey(Platform("telegram"), "main", "chat:42")
            result = await pipeline._submit_to_pool(msg, pool, key)

        assert result.action == Action.SUBMIT_TO_POOL
        assert notify_calls == []
