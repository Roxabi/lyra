"""Tests for lyra.core.pool.pool_observer.PoolObserver.

Covers:
- turn logging (log_turn_async) with/without TurnStore
- session persistence (session_update_async) one-shot guard
- fire-and-forget error handling
- append() wires turn-logger + log_turn_async
- reset_session_persisted() re-enables persistence
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from lyra.core.pool.pool_observer import PoolObserver
from tests.core.conftest import make_inbound_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POOL_ID = "pool:tg:chat:42"
_SESSION_ID = "sess-abcdef01"


def _make_observer(session_id: str = _SESSION_ID) -> PoolObserver:
    return PoolObserver(pool_id=_POOL_ID, session_id_fn=lambda: session_id)


# ---------------------------------------------------------------------------
# log_turn_async
# ---------------------------------------------------------------------------


class TestLogTurnAsync:
    async def test_noop_when_no_turn_store(self) -> None:
        """log_turn_async is silent when no TurnStore is registered."""
        obs = _make_observer()
        # Should not raise
        obs.log_turn_async(
            role="user",
            platform="telegram",
            user_id="alice",
            content="hello",
        )

    async def test_calls_turn_store_log_turn(self) -> None:
        """log_turn_async fires a task calling turn_store.log_turn."""
        obs = _make_observer()
        store = MagicMock()
        store.log_turn = AsyncMock(return_value=None)
        obs.register_turn_store(store)

        obs.log_turn_async(
            role="user",
            platform="telegram",
            user_id="alice",
            content="hello",
            message_id="msg-1",
        )
        # Let fire-and-forget task run
        await asyncio.sleep(0)

        store.log_turn.assert_called_once_with(
            pool_id=_POOL_ID,
            session_id=_SESSION_ID,
            role="user",
            platform="telegram",
            user_id="alice",
            content="hello",
            message_id="msg-1",
            reply_message_id=None,
        )

    async def test_uses_current_session_id_from_fn(self) -> None:
        """session_id_fn is called at log time, not at construction time."""
        session_ids = ["sess-first"]
        obs = PoolObserver(pool_id=_POOL_ID, session_id_fn=lambda: session_ids[-1])

        store = MagicMock()
        store.log_turn = AsyncMock(return_value=None)
        obs.register_turn_store(store)

        session_ids.append("sess-second")
        obs.log_turn_async(
            role="assistant", platform="telegram", user_id="bot", content="hi"
        )
        await asyncio.sleep(0)

        _, kwargs = store.log_turn.call_args
        assert kwargs["session_id"] == "sess-second"


# ---------------------------------------------------------------------------
# session_update_async
# ---------------------------------------------------------------------------


class TestSessionUpdateAsync:
    async def test_noop_when_no_callback(self) -> None:
        """session_update_async is silent when no callback is registered."""
        obs = _make_observer()
        msg = make_inbound_message()
        obs.session_update_async(msg)  # should not raise

    async def test_calls_session_update_fn(self) -> None:
        """session_update_async fires the registered callback."""
        obs = _make_observer()
        callback = AsyncMock(return_value=None)
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()
        obs.session_update_async(msg)
        await asyncio.sleep(0)

        callback.assert_called_once_with(msg, _SESSION_ID, _POOL_ID)

    async def test_one_shot_guard(self) -> None:
        """session_update_async fires only once per session cycle."""
        obs = _make_observer()
        callback = AsyncMock(return_value=None)
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()
        obs.session_update_async(msg)
        obs.session_update_async(msg)  # second call — must be ignored
        await asyncio.sleep(0)

        callback.assert_called_once()

    async def test_reset_session_persisted_re_enables(self) -> None:
        """After reset_session_persisted(), next session_update_async fires again."""
        obs = _make_observer()
        callback = AsyncMock(return_value=None)
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()
        obs.session_update_async(msg)
        await asyncio.sleep(0)
        assert callback.call_count == 1

        obs.reset_session_persisted()
        obs.session_update_async(msg)
        await asyncio.sleep(0)
        assert callback.call_count == 2


# ---------------------------------------------------------------------------
# fire_and_forget error handling
# ---------------------------------------------------------------------------


class TestFireAndForget:
    async def test_error_in_coro_does_not_propagate(self) -> None:
        """Exceptions in fire-and-forget coroutines never propagate to the caller."""
        obs = _make_observer()

        async def _bad() -> None:
            raise ValueError("oops")

        # Must not raise — exception is swallowed and logged internally
        obs._fire_and_forget(_bad(), "test-label")
        await asyncio.sleep(0)  # drain event loop

    async def test_no_event_loop_does_not_raise(self) -> None:
        """_fire_and_forget silently no-ops when there is no running event loop."""
        obs = _make_observer()

        async def _coro() -> None:
            pass

        obs._fire_and_forget(_coro(), "safe-label")  # must not raise


# ---------------------------------------------------------------------------
# append()
# ---------------------------------------------------------------------------


class TestAppend:
    async def test_append_fires_turn_logger(self) -> None:
        """append() calls the registered turn_logger with session_id + msg."""
        obs = _make_observer()
        turn_logger = AsyncMock(return_value=None)
        obs.register_turn_logger(turn_logger)

        msg = make_inbound_message()
        obs.append(msg, session_id=_SESSION_ID)
        await asyncio.sleep(0)

        turn_logger.assert_called_once_with(_SESSION_ID, msg)

    async def test_append_logs_user_turn_via_turn_store(self) -> None:
        """append() also logs the user turn through TurnStore."""
        obs = _make_observer()
        store = MagicMock()
        store.log_turn = AsyncMock(return_value=None)
        obs.register_turn_store(store)

        msg = make_inbound_message(user_id="bob", platform="discord")
        obs.append(msg, session_id=_SESSION_ID)
        await asyncio.sleep(0)

        _, kwargs = store.log_turn.call_args
        assert kwargs["role"] == "user"
        assert kwargs["user_id"] == "bob"
        assert kwargs["platform"] == "discord"

    async def test_append_noop_when_no_turn_logger(self) -> None:
        """append() silently skips turn_logger when none is registered."""
        obs = _make_observer()
        msg = make_inbound_message()
        obs.append(msg, session_id=_SESSION_ID)  # should not raise


# ---------------------------------------------------------------------------
# MessageIndex registration (#341)
# ---------------------------------------------------------------------------


class TestMessageIndexRegistration:
    def test_register_message_index(self) -> None:
        obs = _make_observer()
        mi = MagicMock()
        obs.register_message_index(mi)
        assert obs._message_index is mi

    def test_register_message_index_none_by_default(self) -> None:
        obs = _make_observer()
        assert obs._message_index is None


# ---------------------------------------------------------------------------
# MessageIndex population in append() (#341)
# ---------------------------------------------------------------------------


class TestMessageIndexPopulation:
    async def test_append_indexes_user_turn(self) -> None:
        """append() upserts user turn into MessageIndex."""
        obs = _make_observer()
        mi = MagicMock()
        mi.upsert = AsyncMock(return_value=None)
        obs.register_message_index(mi)

        msg = make_inbound_message()
        msg.platform_meta["message_id"] = 42  # Telegram int
        obs.append(msg, session_id=_SESSION_ID)
        await asyncio.sleep(0)

        mi.upsert.assert_called_once_with(_POOL_ID, "42", _SESSION_ID, "user")

    async def test_append_skips_when_no_message_index(self) -> None:
        """append() does not fail when no MessageIndex is registered."""
        obs = _make_observer()
        msg = make_inbound_message()
        msg.platform_meta["message_id"] = 42
        obs.append(msg, session_id=_SESSION_ID)  # should not raise

    async def test_append_skips_when_no_message_id_in_meta(self) -> None:
        """append() skips index when platform_meta has no message_id."""
        obs = _make_observer()
        mi = MagicMock()
        mi.upsert = AsyncMock(return_value=None)
        obs.register_message_index(mi)

        msg = make_inbound_message()
        # No message_id in platform_meta
        msg.platform_meta.pop("message_id", None)
        obs.append(msg, session_id=_SESSION_ID)
        await asyncio.sleep(0)

        mi.upsert.assert_not_called()
