"""Integration test — reply-to while pool busy sets _pending_session_id."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.hub.path_validation import resolve_context
from lyra.core.message import InboundMessage
from lyra.core.trust import TrustLevel

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(pool_id: str = "telegram:main:chat:42"):
    from lyra.core.pool import Pool

    ctx = MagicMock()
    ctx.get_agent = MagicMock(return_value=None)
    ctx.get_message = MagicMock(return_value=None)
    ctx.dispatch_response = AsyncMock()
    ctx.dispatch_streaming = AsyncMock()
    ctx.record_circuit_success = MagicMock()
    ctx.record_circuit_failure = MagicMock()
    return Pool(pool_id, "lyra", ctx)


def _make_msg(**kwargs) -> InboundMessage:
    defaults = dict(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.PUBLIC,
        reply_to_id=None,
    )
    defaults.update(kwargs)
    return InboundMessage(**defaults)  # type: ignore[arg-type]


class _FakeMessageIndex:
    """Returns a fixed session_id for a given message id."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    async def resolve(self, pool_id: str, msg_id: str) -> str | None:
        return self._mapping.get(msg_id)


class _FakeTurnStore:
    async def get_last_session(self, pool_id: str) -> str | None:
        return None

    async def increment_resume_count(self, session_id: str) -> None:
        pass

    async def get_session_pool_id(self, session_id: str) -> str | None:
        return None

    async def log_turn(self, **_kwargs) -> None:
        pass


class _FakeHub:
    """Minimal hub stub for SubmitToPoolMiddleware."""

    def __init__(self, pool, message_index=None, turn_store=None) -> None:
        self._message_index = message_index
        self._turn_store = turn_store
        self.adapter_registry = {("telegram", "main"): MagicMock()}
        self.agent_registry: dict = {}
        self.circuit_registry = None

    async def circuit_breaker_drop(self, msg) -> bool:  # noqa: ANN001
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pending_session_id_set_when_pool_busy() -> None:
    """When pool is busy and reply-to arrives, _pending_session_id is set."""
    pool = _make_pool("telegram:main:chat:42")

    # Make pool appear busy
    pool._current_task = asyncio.create_task(asyncio.sleep(100))

    try:
        message_index = _FakeMessageIndex({"msg-old": "session-abc"})
        turn_store = _FakeTurnStore()

        fake_hub = _FakeHub(pool, message_index=message_index, turn_store=turn_store)

        msg = _make_msg(
            id="msg-new",
            reply_to_id="msg-old",
            platform="telegram",
            bot_id="main",
            scope_id="chat:42",
        )

        ctx = MagicMock(hub=fake_hub)
        await resolve_context(msg, pool, pool.pool_id, ctx)

        assert pool._pending_session_id == "session-abc", (  # type: ignore[attr-defined]
            "pool._pending_session_id must be set when pool busy and reply-to resolves"
        )
    finally:
        pool._current_task.cancel()
        try:
            await pool._current_task
        except asyncio.CancelledError:
            pass


async def test_pending_session_id_not_set_when_pool_idle() -> None:
    """When pool is idle, reply-to should attempt resume directly (not queue)."""
    pool = _make_pool("telegram:main:chat:42")

    # Pool is idle by default (no current task)
    assert pool.is_idle

    assert pool._pending_session_id is None, (  # type: ignore[attr-defined]
        "idle pool must start with _pending_session_id=None"
    )


async def test_pending_session_id_none_when_no_reply_to() -> None:
    """Without reply_to_id, _pending_session_id should remain None."""
    pool = _make_pool("telegram:main:chat:42")

    assert pool._pending_session_id is None, (  # type: ignore[attr-defined]
        "_pending_session_id must be None when no reply-to was received"
    )


async def test_process_loop_fires_pending_session_id() -> None:
    """process_loop must consume _pending_session_id and call resume_session."""
    pool = _make_pool("telegram:main:chat:42")

    # Disable debounce so collect() returns immediately without a 300 ms wait
    pool.debounce_ms = 0

    # Wire a resume callback
    resume_fn = AsyncMock(return_value=True)
    pool._session_resume_fn = resume_fn

    # Set a pending session before the loop runs
    pool._pending_session_id = "target-session-id"  # type: ignore[attr-defined]

    # Make get_agent() raise so process_loop exits quickly after the resume step
    pool._ctx.get_agent = MagicMock(side_effect=RuntimeError("stop here"))

    msg = _make_msg(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
    )

    # Wire an on_resume callback
    on_resume_fn = AsyncMock()
    pool._on_resume_fn = on_resume_fn

    # Submit triggers process_loop via asyncio.create_task
    pool.submit(msg)

    # Give the event loop enough time to run through the resume step
    await asyncio.sleep(0.1)

    resume_fn.assert_awaited_once_with("target-session-id")
    on_resume_fn.assert_awaited_once_with("target-session-id")


async def test_process_loop_does_not_call_on_resume_fn_when_resume_rejected() -> None:
    """process_loop must NOT call _on_resume_fn when resume_session returns False."""
    pool = _make_pool("telegram:main:chat:42")

    pool.debounce_ms = 0

    resume_fn = AsyncMock(return_value=False)
    on_resume_fn = AsyncMock()
    pool._session_resume_fn = resume_fn
    pool._on_resume_fn = on_resume_fn

    pool._pending_session_id = "target-session-id"  # type: ignore[attr-defined]

    pool._ctx.get_agent = MagicMock(side_effect=RuntimeError("stop here"))

    msg = _make_msg(
        id="msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
    )

    pool.submit(msg)

    await asyncio.sleep(0.1)

    on_resume_fn.assert_not_awaited()
