"""Tests for MessagePipeline._resolve_context() — reply-to-resume,
MessageIndex integration (#244, #341), and session-fallthrough notification (#380)."""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import patch

from lyra.core.hub.message_pipeline import Action, MessagePipeline, ResumeStatus
from tests.core.conftest import _make_hub, make_inbound_message

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
        hub._message_index = mi  # type: ignore[assignment]
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-99")
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert resumed == ["sess-1"]

    async def test_no_resume_when_reply_to_id_none(self) -> None:
        """When msg.reply_to_id is None, MessageIndex is never called."""
        mi = _StubMessageIndex()
        hub = _make_hub()
        hub._message_index = mi  # type: ignore[assignment]
        pool_id = "telegram:main:chat:42"
        pool = hub.get_or_create_pool(pool_id, "lyra")

        msg = make_inbound_message(scope_id="chat:42")
        assert msg.reply_to_id is None

        pipeline = MessagePipeline(hub)
        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert mi.resolve_calls == []

    async def test_no_resume_when_not_found(self) -> None:
        """When MessageIndex returns None, resume is not called (fallthrough)."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex()  # empty — resolve returns None
        hub = _make_hub()
        hub._message_index = mi  # type: ignore[assignment]
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-77")
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert mi.resolve_calls == [(pool_id, "tg-msg-77")]
        assert resumed == []

    async def test_no_resume_when_pool_busy(self) -> None:
        """When pool.is_idle is False, resume is skipped."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "tg-msg-88"): "sess-busy"})
        hub = _make_hub()
        hub._message_index = mi  # type: ignore[assignment]

        pool = hub.get_or_create_pool(pool_id, "lyra")
        pool._current_task = asyncio.create_task(asyncio.sleep(10))

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-88")
        pipeline = MessagePipeline(hub)

        try:
            await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]
        finally:
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
            pool._current_task = None

        assert resumed == []

    async def test_no_resume_in_group_chat(self) -> None:
        """Group chat guard prevents resume (cross-user risk)."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "tg-msg-55"): "sess-group"})
        hub = _make_hub()
        hub._message_index = mi  # type: ignore[assignment]

        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            reply_to_id="tg-msg-55",
            platform_meta={**_base.platform_meta, "is_group": True},
        )
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert mi.resolve_calls == [(pool_id, "tg-msg-55")]
        assert resumed == []

    async def test_no_resume_when_message_index_none(self) -> None:
        """When hub._message_index is None, Path 1 is skipped entirely."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        assert hub._message_index is None
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-msg-42")
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

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
        hub._message_index = mi  # type: ignore[assignment]
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="msg-100")
        pipeline = MessagePipeline(hub)

        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert mi.resolve_calls == [(pool_id, "msg-100")]
        assert resumed == ["sess-abc"]

    async def test_resolve_fallback_when_not_found(self) -> None:
        """When MessageIndex returns None, Path 3 fallback is tried."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex()  # empty
        hub = _make_hub()
        hub._message_index = mi  # type: ignore[assignment]
        pool = hub.get_or_create_pool(pool_id, "lyra")

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="unknown-msg")
        pipeline = MessagePipeline(hub)

        # Should not raise — falls through to Path 3
        await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert mi.resolve_calls == [(pool_id, "unknown-msg")]

    async def test_resolve_skips_when_pool_busy(self) -> None:
        """Busy pool prevents resume even when MessageIndex has a match."""
        pool_id = "telegram:main:chat:42"
        mi = _StubMessageIndex({(pool_id, "msg-busy"): "sess-x"})
        hub = _make_hub()
        hub._message_index = mi  # type: ignore[assignment]
        pool = hub.get_or_create_pool(pool_id, "lyra")
        pool._current_task = asyncio.create_task(asyncio.sleep(10))

        resumed: list[str] = []

        async def _fake_resume(sid: str) -> None:
            resumed.append(sid)

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="msg-busy")
        pipeline = MessagePipeline(hub)

        try:
            await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]
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
        hub._message_index = mi  # type: ignore[assignment]
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(_base, reply_to_id="tg-99")
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert status == ResumeStatus.RESUMED

    async def test_path2_accepted_returns_resumed(self) -> None:
        """Path 2 accepted → RESUMED."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-1"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert status == ResumeStatus.RESUMED

    async def test_path2_rejected_no_path3_returns_fresh(self) -> None:
        """Path 2 rejected, no TurnStore → FRESH (user must be notified)."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return False  # session pruned / invalid

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

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

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        class _FakeTurnStore:
            async def get_last_session(self, pid: str) -> str | None:
                return "last-sess"

            async def close(self) -> None:
                pass

        hub._turn_store = _FakeTurnStore()  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

        assert status == ResumeStatus.RESUMED
        assert len(resume_calls) == 2  # path 2 + path 3

    async def test_no_thread_session_no_turn_store_returns_skipped(self) -> None:
        """No thread_session_id and no TurnStore → SKIPPED (first-use, silent)."""
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        msg = make_inbound_message(scope_id="chat:42")
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

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
            status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]
        finally:
            pool._current_task.cancel()
            try:
                await pool._current_task
            except asyncio.CancelledError:
                pass
            pool._current_task = None

        assert status == ResumeStatus.SKIPPED

    async def test_path2_rejected_group_chat_returns_skipped(self) -> None:
        """Path 2 rejected in a group chat → SKIPPED (silent, no notification).

        Group-chat resume is a deliberate safety skip regardless of whether
        path 2 was attempted — broadcasting session-state into a shared channel
        would leak per-user context to all participants.
        """
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _rejected_resume(sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume  # type: ignore[attr-defined]

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {
            **_base.platform_meta,
            "thread_session_id": "tss-dead",
            "is_group": True,
        }
        msg = dataclasses.replace(_base, platform_meta=_meta)
        pipeline = MessagePipeline(hub)

        status = await pipeline._resolve_context(msg, pool, pool_id)  # type: ignore[attr-defined]

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

        pool._session_resume_fn = _rejected_resume  # type: ignore[attr-defined]

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
            result = await pipeline._submit_to_pool(msg, pool, key)  # type: ignore[attr-defined]

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

        pool._session_resume_fn = _accepted_resume  # type: ignore[attr-defined]

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
            result = await pipeline._submit_to_pool(msg, pool, key)  # type: ignore[attr-defined]

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
            result = await pipeline._submit_to_pool(msg, pool, key)  # type: ignore[attr-defined]

        assert result.action == Action.SUBMIT_TO_POOL
        assert notify_calls == []
