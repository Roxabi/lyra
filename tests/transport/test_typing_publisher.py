"""Tests for TypingPublisher (#1376) — AC2/AC3/AC4/AC5.

RED phase: written before TypingPublisher exists. Will collect-fail with
ImportError until T3 lands the impl in Wave 2.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from lyra.transport.typing_publisher import TypingPublisher
from lyra.transport.work_scope import WorkScope


@pytest.fixture
def scope() -> WorkScope:
    return WorkScope(platform="telegram", bot_id="x", scope_id=1, trace_id="abc")


@pytest.mark.asyncio
async def test_refcount_two_started_one_publish(scope: WorkScope) -> None:
    nc = AsyncMock()
    pub = TypingPublisher(nc, enabled=True)
    await pub.publish_started(scope)
    await pub.publish_started(scope)
    assert nc.publish.call_count == 1  # AC2 — ref-count idempotence on started


@pytest.mark.asyncio
async def test_two_ended_one_publish(scope: WorkScope) -> None:
    nc = AsyncMock()
    pub = TypingPublisher(nc, enabled=True)
    await pub.publish_started(scope)
    await pub.publish_started(scope)
    nc.publish.reset_mock()
    await pub.publish_ended(scope)
    assert nc.publish.call_count == 0  # AC2 — refcount=1, no wire
    await pub.publish_ended(scope)
    assert nc.publish.call_count == 1  # AC2 — refcount=0, single wire


@pytest.mark.asyncio
async def test_ended_unknown_scope_noop(scope: WorkScope) -> None:
    nc = AsyncMock()
    pub = TypingPublisher(nc, enabled=True)
    await pub.publish_ended(scope)  # AC3 — idempotent
    nc.publish.assert_not_called()


@pytest.mark.asyncio
async def test_flag_off_no_publish(scope: WorkScope) -> None:
    nc = AsyncMock()
    pub = TypingPublisher(nc, enabled=False)
    await pub.publish_started(scope)
    await pub.publish_ended(scope)
    assert nc.publish.call_count == 0  # AC4 — flag-off = zero wire traffic


@pytest.mark.asyncio
async def test_publish_error_swallow(scope: WorkScope) -> None:
    """AC5: publish error swallowed; ref-count NOT rolled back (best-effort)."""
    nc = AsyncMock()
    nc.publish.side_effect = RuntimeError("boom")
    pub = TypingPublisher(nc, enabled=True)
    await pub.publish_started(scope)  # AC5 — must not raise
    # AC5 — best-effort: ref-count remains incremented despite publish failure.
    # A regression that rolled back on error would fail this assertion.
    key = (scope.platform, scope.bot_id, scope.scope_id)
    assert pub._refcount[key] == 1


@pytest.mark.asyncio
async def test_publish_ended_error_swallow(scope: WorkScope) -> None:
    """AC5 (sibling): publish_ended swallows error AND ref-count is decremented to 0
    (the impl decrements before _publish call, so rollback would re-increment)."""
    nc = AsyncMock()
    pub = TypingPublisher(nc, enabled=True)
    await pub.publish_started(scope)  # ref-count → 1, one successful publish
    key = (scope.platform, scope.bot_id, scope.scope_id)
    assert pub._refcount[key] == 1

    nc.publish.side_effect = RuntimeError("boom")
    await pub.publish_ended(scope)  # AC5 — must not raise

    # AC5 best-effort: decrement before _publish error; no re-increment rollback.
    # Key may be removed (count → 0) per the impl's del branch.
    assert key not in pub._refcount


# ---------------------------------------------------------------------------
# T10 — scope() context manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_calls_publish_started_on_entry(scope: WorkScope) -> None:
    """scope() calls publish_started on entry."""
    pub = TypingPublisher(AsyncMock(), enabled=True)
    pub.publish_started = AsyncMock()
    pub.publish_ended = AsyncMock()
    async with pub.scope(scope):
        pub.publish_ended.assert_not_awaited()
    pub.publish_started.assert_awaited_once_with(scope)


@pytest.mark.asyncio
async def test_scope_calls_publish_ended_on_normal_exit(scope: WorkScope) -> None:
    """scope() calls publish_ended on normal exit."""
    pub = TypingPublisher(AsyncMock(), enabled=True)
    pub.publish_started = AsyncMock()
    pub.publish_ended = AsyncMock()
    pub.publish_started.assert_not_awaited()
    async with pub.scope(scope):
        pass
    pub.publish_ended.assert_awaited_once_with(scope)


@pytest.mark.asyncio
async def test_scope_calls_publish_ended_on_exception_exit(scope: WorkScope) -> None:
    """scope() calls publish_ended even when the body raises an exception."""
    pub = TypingPublisher(AsyncMock(), enabled=True)
    pub.publish_started = AsyncMock()
    pub.publish_ended = AsyncMock()
    pub.publish_started.assert_not_awaited()
    with pytest.raises(RuntimeError, match="boom"):
        async with pub.scope(scope):
            raise RuntimeError("boom")
    pub.publish_ended.assert_awaited_once_with(scope)


@pytest.mark.asyncio
async def test_scope_calls_publish_ended_on_cancelled_error(scope: WorkScope) -> None:
    """scope() calls publish_ended even when the body raises CancelledError."""
    pub = TypingPublisher(AsyncMock(), enabled=True)
    pub.publish_started = AsyncMock()
    pub.publish_ended = AsyncMock()
    pub.publish_started.assert_not_awaited()
    with pytest.raises(asyncio.CancelledError):
        async with pub.scope(scope):
            raise asyncio.CancelledError()
    pub.publish_ended.assert_awaited_once_with(scope)
