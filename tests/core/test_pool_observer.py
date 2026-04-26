"""Tests for lyra.core.pool.pool_observer.PoolObserver.

Covers:
- turn logging (log_turn_async) with/without TurnStore
- session persistence (session_update_async) one-shot guard
- append() wires turn-logger + log_turn_async
- reset_session_persisted() re-enables persistence
"""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.messaging.message import TelegramMeta
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
    @pytest.mark.anyio
    async def test_noop_when_no_turn_store(self) -> None:
        """log_turn_async is silent when no TurnStore is registered."""
        obs = _make_observer()
        await obs.log_turn_async(
            role="user",
            platform="telegram",
            user_id="alice",
            content="hello",
        )

    @pytest.mark.anyio
    async def test_calls_turn_store_log_turn(self) -> None:
        """log_turn_async awaits turn_store.log_turn."""
        obs = _make_observer()
        store = MagicMock()
        store.log_turn = AsyncMock(return_value=None)
        obs.register_turn_store(store)

        await obs.log_turn_async(
            role="user",
            platform="telegram",
            user_id="alice",
            content="hello",
            message_id="msg-1",
        )

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

    @pytest.mark.anyio
    async def test_uses_current_session_id_from_fn(self) -> None:
        """session_id_fn is called at log time, not at construction time."""
        session_ids = ["sess-first"]
        obs = PoolObserver(pool_id=_POOL_ID, session_id_fn=lambda: session_ids[-1])

        store = MagicMock()
        store.log_turn = AsyncMock(return_value=None)
        obs.register_turn_store(store)

        session_ids.append("sess-second")
        await obs.log_turn_async(
            role="assistant", platform="telegram", user_id="bot", content="hi"
        )

        _, kwargs = store.log_turn.call_args
        assert kwargs["session_id"] == "sess-second"


# ---------------------------------------------------------------------------
# session_update_async
# ---------------------------------------------------------------------------


class TestSessionUpdateAsync:
    @pytest.mark.anyio
    async def test_noop_when_no_callback(self) -> None:
        """session_update_async is silent when no callback is registered."""
        obs = _make_observer()
        msg = make_inbound_message()
        await obs.session_update_async(msg)  # should not raise

    @pytest.mark.anyio
    async def test_calls_session_update_fn(self) -> None:
        """session_update_async awaits the registered callback."""
        obs = _make_observer()
        callback = AsyncMock(return_value=None)
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()
        await obs.session_update_async(msg)

        callback.assert_called_once_with(msg, _SESSION_ID, _POOL_ID)

    @pytest.mark.anyio
    async def test_one_shot_guard(self) -> None:
        """session_update_async fires only once per session cycle."""
        obs = _make_observer()
        callback = AsyncMock(return_value=None)
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()
        await obs.session_update_async(msg)
        await obs.session_update_async(msg)  # second call — must be ignored

        callback.assert_called_once()

    @pytest.mark.anyio
    async def test_reset_session_persisted_re_enables(self) -> None:
        """After reset_session_persisted(), next session_update_async fires again."""
        obs = _make_observer()
        callback = AsyncMock(return_value=None)
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()
        await obs.session_update_async(msg)
        assert callback.call_count == 1

        obs.reset_session_persisted()
        await obs.session_update_async(msg)
        assert callback.call_count == 2


# ---------------------------------------------------------------------------
# append()
# ---------------------------------------------------------------------------


class TestAppend:
    @pytest.mark.anyio
    async def test_append_fires_turn_logger(self) -> None:
        """append() awaits the registered turn_logger with session_id + msg."""
        obs = _make_observer()
        turn_logger = AsyncMock(return_value=None)
        obs.register_turn_logger(turn_logger)

        msg = make_inbound_message()
        await obs.append(msg, session_id=_SESSION_ID)

        turn_logger.assert_called_once_with(_SESSION_ID, msg)

    @pytest.mark.anyio
    async def test_append_logs_user_turn_via_turn_store(self) -> None:
        """append() also logs the user turn through TurnStore."""
        obs = _make_observer()
        store = MagicMock()
        store.log_turn = AsyncMock(return_value=None)
        obs.register_turn_store(store)

        msg = make_inbound_message(user_id="bob", platform="discord")
        await obs.append(msg, session_id=_SESSION_ID)

        _, kwargs = store.log_turn.call_args
        assert kwargs["role"] == "user"
        assert kwargs["user_id"] == "bob"
        assert kwargs["platform"] == "discord"

    @pytest.mark.anyio
    async def test_append_noop_when_no_turn_logger(self) -> None:
        """append() silently skips turn_logger when none is registered."""
        obs = _make_observer()
        msg = make_inbound_message()
        await obs.append(msg, session_id=_SESSION_ID)  # should not raise


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
    @pytest.mark.anyio
    async def test_append_indexes_user_turn(self) -> None:
        """append() upserts user turn into MessageIndex."""
        obs = _make_observer()
        mi = MagicMock()
        mi.upsert = AsyncMock(return_value=None)
        obs.register_message_index(mi)

        msg = make_inbound_message()
        assert isinstance(msg.platform_meta, TelegramMeta)
        msg = dataclasses.replace(
            msg,
            platform_meta=dataclasses.replace(msg.platform_meta, message_id=42),
        )
        await obs.append(msg, session_id=_SESSION_ID)

        mi.upsert.assert_called_once_with(_POOL_ID, "42", _SESSION_ID, "user")

    @pytest.mark.anyio
    async def test_append_skips_when_no_message_index(self) -> None:
        """append() does not fail when no MessageIndex is registered."""
        obs = _make_observer()
        msg = make_inbound_message()
        assert isinstance(msg.platform_meta, TelegramMeta)
        msg = dataclasses.replace(
            msg,
            platform_meta=dataclasses.replace(msg.platform_meta, message_id=42),
        )
        await obs.append(msg, session_id=_SESSION_ID)  # should not raise

    @pytest.mark.anyio
    async def test_append_skips_when_no_message_id_in_meta(self) -> None:
        """append() skips index when platform_meta has no message_id."""
        obs = _make_observer()
        mi = MagicMock()
        mi.upsert = AsyncMock(return_value=None)
        obs.register_message_index(mi)

        msg = make_inbound_message()
        assert isinstance(msg.platform_meta, TelegramMeta)
        # Set message_id to None — observer should skip indexing
        msg = dataclasses.replace(
            msg,
            platform_meta=dataclasses.replace(msg.platform_meta, message_id=None),
        )
        await obs.append(msg, session_id=_SESSION_ID)

        mi.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Error path / try-except coverage (#FindingH)
# ---------------------------------------------------------------------------


class TestLogTurnAsyncErrorPath:
    @pytest.mark.anyio
    async def test_error_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_turn_async: TurnStore exception is caught, does not raise."""
        import logging

        obs = _make_observer()
        store = MagicMock()
        store.log_turn = AsyncMock(side_effect=RuntimeError("DB error"))
        obs.register_turn_store(store)

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool.pool_observer"):
            # Act — must not raise
            await obs.log_turn_async(
                role="user",
                platform="telegram",
                user_id="alice",
                content="hello",
            )

        assert any("turn_store write failed" in r.message for r in caplog.records)


class TestSessionUpdateAsyncErrorPath:
    @pytest.mark.anyio
    async def test_error_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """session_update_async catches callback exceptions and logs ERROR."""
        import logging

        obs = _make_observer()
        callback = AsyncMock(side_effect=RuntimeError("session DB error"))
        obs.register_session_update_fn(callback)

        msg = make_inbound_message()

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool.pool_observer"):
            # Act — must not raise
            await obs.session_update_async(msg)

        assert any("session_update failed" in r.message for r in caplog.records)


class TestAppendErrorPath:
    @pytest.mark.anyio
    async def test_turn_logger_error_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """append() catches turn_logger exceptions and logs ERROR, does not raise."""
        import logging

        obs = _make_observer()
        turn_logger = AsyncMock(side_effect=RuntimeError("logger boom"))
        obs.register_turn_logger(turn_logger)

        msg = make_inbound_message()

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool.pool_observer"):
            # Act — must not raise
            await obs.append(msg, session_id=_SESSION_ID)

        assert any("turn_logger failed" in r.message for r in caplog.records)


class TestIndexTurnAsyncErrorPath:
    @pytest.mark.anyio
    async def test_error_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """index_turn_async catches MessageIndex exceptions and logs ERROR."""
        import logging

        obs = _make_observer()
        mi = MagicMock()
        mi.upsert = AsyncMock(side_effect=RuntimeError("index DB error"))
        obs.register_message_index(mi)

        with caplog.at_level(logging.ERROR, logger="lyra.core.pool.pool_observer"):
            # Act — must not raise
            await obs.index_turn_async("msg-42", session_id=_SESSION_ID, role="user")

        assert any("message_index upsert failed" in r.message for r in caplog.records)
