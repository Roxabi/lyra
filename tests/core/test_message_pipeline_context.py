"""Tests for MessagePipeline._resolve_context() — reply-to-resume and
MessageIndex integration (#244, #341)."""

from __future__ import annotations

import asyncio
import dataclasses

from lyra.core.message_pipeline import MessagePipeline
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
