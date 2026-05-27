"""Integration test — /clear rotates session UUID and publishes via TurnPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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


class _FakeTurnPublisher:
    """Inline fake that records publish_end_session / publish_start_session calls."""

    def __init__(self) -> None:
        self.ended: list[str] = []
        self.started: list[tuple[str, str]] = []  # (session_id, pool_id)
        self._call_order: list[str] = []

    async def publish_end_session(
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        trace_id: str,
    ) -> None:
        self.ended.append(session_id)
        self._call_order.append(f"end:{session_id}")

    async def publish_start_session(
        self,
        *,
        pool_id: str,
        session_id: str,
        platform: str,
        user_id: str,
        trace_id: str,
    ) -> None:
        self.started.append((session_id, pool_id))
        self._call_order.append(f"start:{session_id}")

    async def publish_log_turn(self, **_kwargs) -> None:  # noqa: ANN003
        pass

    async def publish_increment_resume_count(self, **_kwargs) -> None:  # noqa: ANN003
        pass


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
    """pool.reset_session() must publish end_session(before_sid) via TurnPublisher."""
    pool = _make_pool("telegram:main:chat:42")
    fake_pub = _FakeTurnPublisher()
    pool._observer.register_turn_publisher(fake_pub)  # type: ignore[arg-type]

    before_sid = pool.session_id

    await pool.reset_session()

    assert before_sid in fake_pub.ended, (
        f"publish_end_session({before_sid!r}) was not called on TurnPublisher"
    )


async def test_reset_session_calls_start_session_on_turn_store() -> None:
    """pool.reset_session() must publish start_session(new_sid, pool_id)."""
    pool = _make_pool("telegram:main:chat:42")
    fake_pub = _FakeTurnPublisher()
    pool._observer.register_turn_publisher(fake_pub)  # type: ignore[arg-type]

    await pool.reset_session()

    assert len(fake_pub.started) >= 1, (
        "publish_start_session() was not called on TurnPublisher after reset_session()"
    )
    new_sid, recorded_pool_id = fake_pub.started[0]
    assert recorded_pool_id == "telegram:main:chat:42"
    assert new_sid == pool.session_id


async def test_reset_session_end_before_start() -> None:
    """publish_end_session must be called before publish_start_session."""
    pool = _make_pool("telegram:main:chat:42")
    fake_pub = _FakeTurnPublisher()
    pool._observer.register_turn_publisher(fake_pub)  # type: ignore[arg-type]
    before_sid = pool.session_id

    await pool.reset_session()

    assert fake_pub._call_order[0] == f"end:{before_sid}", (
        "publish_end_session must be called before publish_start_session"
    )
    assert fake_pub._call_order[1].startswith("start:"), (
        "publish_start_session must be called after publish_end_session"
    )
