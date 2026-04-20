"""Integration test — /clear rotates Pool session UUID and notifies TurnStore."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.infrastructure.stores.turn_store import TurnStore

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


class _FakeTurnStore:
    """Inline fake that records end_session / start_session calls."""

    def __init__(self) -> None:
        self.ended: list[str] = []
        self.started: list[tuple[str, str]] = []

    async def end_session(self, session_id: str) -> None:
        self.ended.append(session_id)

    async def start_session(self, session_id: str, pool_id: str) -> None:
        self.started.append((session_id, pool_id))

    # Stubs for other TurnStore methods the observer may call
    async def log_turn(self, **_kwargs) -> None:  # noqa: ANN003
        pass

    async def get_last_session(self, pool_id: str) -> str | None:
        return None

    async def increment_resume_count(self, session_id: str) -> None:
        pass

    async def get_session_pool_id(self, session_id: str) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reset_session_rotates_uuid() -> None:
    """pool.reset_session() must produce a new session_id (UUID rotation)."""
    pool = _make_pool("telegram:main:chat:42")

    before_sid = pool.session_id

    await pool.reset_session()

    after_sid = pool.session_id

    assert before_sid != after_sid, (
        "reset_session() must rotate session_id — UUID was not changed"
    )


async def test_reset_session_calls_end_session_on_turn_store() -> None:
    """pool.reset_session() must call TurnStore.end_session(before_sid)."""
    pool = _make_pool("telegram:main:chat:42")
    fake_store = _FakeTurnStore()
    pool._observer._turn_store = cast(TurnStore, fake_store)

    before_sid = pool.session_id

    await pool.reset_session()

    assert before_sid in fake_store.ended, (
        f"end_session({before_sid!r}) was not called on TurnStore"
    )


async def test_reset_session_calls_start_session_on_turn_store() -> None:
    """pool.reset_session() must call TurnStore.start_session(new_sid, pool_id)."""
    pool = _make_pool("telegram:main:chat:42")
    fake_store = _FakeTurnStore()
    pool._observer._turn_store = cast(TurnStore, fake_store)

    await pool.reset_session()

    assert len(fake_store.started) >= 1, (
        "start_session() was not called on TurnStore after reset_session()"
    )
    new_sid, recorded_pool_id = fake_store.started[0]
    assert recorded_pool_id == "telegram:main:chat:42"
    assert new_sid == pool.session_id


async def test_reset_session_end_before_start() -> None:
    """end_session must be called with the OLD id before start_session is called."""
    pool = _make_pool("telegram:main:chat:42")
    call_order: list[str] = []

    class _OrderedStore(_FakeTurnStore):
        async def end_session(self, session_id: str) -> None:
            call_order.append(f"end:{session_id}")
            await super().end_session(session_id)

        async def start_session(self, session_id: str, pool_id: str) -> None:
            call_order.append(f"start:{session_id}")
            await super().start_session(session_id, pool_id)

    pool._observer._turn_store = _OrderedStore()  # type: ignore
    before_sid = pool.session_id

    await pool.reset_session()

    assert call_order[0] == f"end:{before_sid}", (
        "end_session must be called before start_session"
    )
    assert call_order[1].startswith("start:"), (
        "start_session must be called after end_session"
    )
