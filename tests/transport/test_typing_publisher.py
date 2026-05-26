"""Tests for TypingPublisher (#1376) — AC2/AC3/AC4/AC5.

RED phase: written before TypingPublisher exists. Will collect-fail with
ImportError until T3 lands the impl in Wave 2.
"""

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
    nc = AsyncMock()
    nc.publish.side_effect = RuntimeError("boom")
    pub = TypingPublisher(nc, enabled=True)
    await pub.publish_started(scope)  # AC5 — must not raise
