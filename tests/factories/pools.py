"""Pool factory helpers and fixtures for tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.config import PoolConfig
from lyra.core.messaging.message import InboundMessage, TelegramMeta
from lyra.core.pool import Pool

__all__ = [
    "ctx_mock",
    "fast_pool",
    "make_msg",
    "pool",
    "_make_ctx_mock",
]


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
        config=PoolConfig(turn_timeout=60.0, debounce_ms=0),
    )


@pytest.fixture
def fast_pool(ctx_mock: MagicMock) -> Pool:
    """Pool with a very short timeout for timeout tests."""
    return Pool(
        pool_id="test:main:chat:1",
        agent_name="test_agent",
        ctx=ctx_mock,
        config=PoolConfig(turn_timeout=0.05, debounce_ms=0),
    )


def make_msg(text: str = "hello") -> InboundMessage:
    """Build a minimal InboundMessage for pool tests."""
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
        platform_meta=TelegramMeta(chat_id=1),
        trust_level=TrustLevel.TRUSTED,
    )
