"""Tests for typing_publisher.scope() integration in guarded_process_one (#1377)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.pool import Pool
from lyra.core.pool.pool_processor_exec import guarded_process_one
from lyra.transport.typing_publisher import TypingPublisher
from tests.core.conftest import _make_ctx_mock
from tests.factories.messages import make_inbound_message


class TestGuardedProcessOneTypingScope:
    @pytest.fixture
    def pool(self):
        ctx = _make_ctx_mock()
        pool = Pool(
            pool_id="telegram:main:chat:1",
            agent_name="test_agent",
            ctx=ctx,
        )
        pool._turn_timeout = None
        pool.typing_publisher = TypingPublisher(AsyncMock(), enabled=True)
        pool.typing_publisher.publish_started = AsyncMock()
        pool.typing_publisher.publish_ended = AsyncMock()
        return pool

    @pytest.fixture
    def agent(self):
        agent = MagicMock()
        agent.is_backend_alive = MagicMock(return_value=True)
        agent.reset_backend = AsyncMock()
        return agent

    @pytest.fixture
    def msg(self):
        return make_inbound_message(scope_id="chat:42")

    @pytest.mark.asyncio
    async def test_success_path_scope_entered_and_exited(self, pool, agent, msg):
        """Success path: typing_publisher.scope() is entered and exited."""
        with patch(
            "lyra.core.pool.pool_processor_exec.process_one",
            new=AsyncMock(return_value=None),
        ):
            await guarded_process_one(msg, agent, pool)
            pool.typing_publisher.publish_started.assert_awaited_once()
            pool.typing_publisher.publish_ended.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_path_scope_entered_and_exited(self, pool, agent, msg):
        """Exception path: scope entered and exited even on exception."""
        with patch(
            "lyra.core.pool.pool_processor_exec.process_one",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await guarded_process_one(msg, agent, pool)
            pool.typing_publisher.publish_started.assert_awaited_once()
            pool.typing_publisher.publish_ended.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_path_scope_entered_and_exited(self, pool, agent, msg):
        """Cancellation path: scope entered and exited on CancelledError."""
        with patch(
            "lyra.core.pool.pool_processor_exec.process_one",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await guarded_process_one(msg, agent, pool)
            pool.typing_publisher.publish_started.assert_awaited_once()
            pool.typing_publisher.publish_ended.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_path_scope_entered_and_exited(self, pool, agent, msg):
        """Timeout path: typing_publisher.scope() is entered and exited on timeout."""
        pool._turn_timeout = 0.01

        async def _slow_process(*_a: Any, **_kw: Any) -> None:
            await asyncio.sleep(1.0)

        with patch(
            "lyra.core.pool.pool_processor_exec.process_one",
            new=_slow_process,
        ):
            await guarded_process_one(msg, agent, pool)
            pool.typing_publisher.publish_started.assert_awaited_once()
            pool.typing_publisher.publish_ended.assert_awaited_once()
