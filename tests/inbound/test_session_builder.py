"""Tests for SessionBuilder.build — 4-path session-injection matrix."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    DiscordMeta,
    InboundMessage,
    PlatformMeta,
    TelegramMeta,
)
from lyra.core.stores.thread_store_protocol import ThreadSession
from lyra.inbound.context import SessionCtx
from lyra.inbound.session_builder import SessionBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOT_ID = "main"
_SCOPE_ID = "chat:123"
_POOL_ID = "telegram:main:chat:123"
_SESSION_ID = "sess-abc"


def _make_msg(
    *,
    platform: str = "telegram",
    scope_id: str = _SCOPE_ID,
    bot_id: str = _BOT_ID,
    is_mention: bool = False,
    platform_meta: PlatformMeta | None = None,
) -> InboundMessage:
    if platform_meta is None:
        platform_meta = TelegramMeta()
    return InboundMessage(
        id="msg-1",
        platform=platform,
        bot_id=bot_id,
        scope_id=scope_id,
        user_id="user:42",
        user_name="testuser",
        is_mention=is_mention,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
        platform_meta=platform_meta,
    )


def _make_turn_store(*, last_session: str | None = None) -> MagicMock:
    ts = MagicMock()
    ts.get_last_session = AsyncMock(return_value=last_session)
    ts.start_session = AsyncMock(return_value=None)
    return ts


def _make_thread_store(*, session: ThreadSession | None = None) -> MagicMock:
    th = MagicMock()
    if session is None:
        session = ThreadSession(session_id=None, pool_id=None)
    th.get_session = AsyncMock(return_value=session)
    th.update_session = AsyncMock(return_value=None)
    return th


def _make_session_ctx(
    *,
    turn_store: object = None,
    thread_store: object = None,
    thread_sessions_cache: dict | None = None,
) -> SessionCtx:
    cache: dict = thread_sessions_cache if thread_sessions_cache is not None else {}
    return SessionCtx(
        turn_store=turn_store,  # type: ignore[arg-type]
        thread_store=thread_store,  # type: ignore[arg-type]
        thread_sessions_cache=cache,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionBuilderPathA:
    """Path (a): both stores None — test/CLI mode, msg returned unchanged."""

    @pytest.mark.asyncio
    async def test_both_stores_none_returns_unchanged(self) -> None:
        # Arrange
        builder = SessionBuilder()
        msg = _make_msg(platform_meta=TelegramMeta(chat_id=1))
        ctx = _make_session_ctx(turn_store=None, thread_store=None)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — Negative: removing the `if ts is None and th is None: return msg`
        # guard causes the function to fall into the turnstore path and raise
        # AttributeError (ts is None) or return a different object.
        assert result is msg
        assert result.session_update_fn is None


class TestSessionBuilderPathB:
    """Path (b): turn_store only — Telegram DM."""

    @pytest.mark.asyncio
    async def test_turn_store_only_telegram_dm(self) -> None:
        # Arrange
        ts = _make_turn_store(last_session=_SESSION_ID)
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            platform_meta=TelegramMeta(chat_id=1, is_group=False),
        )
        ctx = _make_session_ctx(turn_store=ts, thread_store=None)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — get_last_session called; session_update_fn attached.
        # Negative: removing the `_build_turnstore_path` call means session_update_fn
        # is never set and the turn handler never persists the session.
        ts.get_last_session.assert_awaited_once()
        assert result.session_update_fn is not None
        # prior session_id propagated onto TelegramMeta.thread_session_id
        assert isinstance(result.platform_meta, TelegramMeta)
        assert result.platform_meta.thread_session_id == _SESSION_ID

        # Closure test: calling session_update_fn triggers start_session.
        await result.session_update_fn(result, "new-sess", "pool-1")
        ts.start_session.assert_awaited_once_with("new-sess", "pool-1")

    @pytest.mark.asyncio
    async def test_turn_store_only_telegram_group_mention(self) -> None:
        # Arrange — Telegram routing already decided PROCESS before SessionBuilder.
        # SessionBuilder uses turn_store regardless of is_group/is_mention.
        ts = _make_turn_store(last_session=None)
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            is_mention=True,
            platform_meta=TelegramMeta(chat_id=5, is_group=True),
        )
        ctx = _make_session_ctx(turn_store=ts, thread_store=None)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — session_update_fn still attached; no prior session_id (last=None).
        # Negative: if SessionBuilder branched on is_group, group messages would skip
        # session wiring and the turn handler would call a None session_update_fn.
        ts.get_last_session.assert_awaited_once()
        assert result.session_update_fn is not None
        # No prior session → thread_session_id not written
        assert isinstance(result.platform_meta, TelegramMeta)
        assert result.platform_meta.thread_session_id is None

        # Closure still fires.
        await result.session_update_fn(result, "sess-new", "pool-2")
        ts.start_session.assert_awaited_once_with("sess-new", "pool-2")


class TestSessionBuilderPathC:
    """Path (c): both stores, Discord DM — turn_store only, thread_store skipped."""

    @pytest.mark.asyncio
    async def test_both_stores_discord_dm_priority(self) -> None:
        # Arrange — Discord DM: guild_id=None, thread_id=None
        ts = _make_turn_store(last_session=_SESSION_ID)
        th = _make_thread_store()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="channel:999",
            platform_meta=DiscordMeta(channel_id=999, guild_id=None, thread_id=None),
        )
        ctx = _make_session_ctx(turn_store=ts, thread_store=th)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — DM path: turn_store consulted, thread_store NOT consulted.
        # Negative: removing the `_discord_thread` guard (thread_id is None check)
        # would route through _build_thread_path, calling th.get_session instead.
        ts.get_last_session.assert_awaited_once()
        th.get_session.assert_not_awaited()
        assert result.session_update_fn is not None

        # Closure writes via turn_store, not thread_store.
        await result.session_update_fn(result, "sess-dm", "pool-dm")
        ts.start_session.assert_awaited_once_with("sess-dm", "pool-dm")
        th.update_session.assert_not_awaited()


class TestSessionBuilderPathD:
    """Path (d): both stores, Discord owned thread — ThreadStore read + write."""

    @pytest.mark.asyncio
    async def test_both_stores_discord_owned_thread(self) -> None:
        # Arrange — owned thread with a prior session in ThreadStore.
        prior = ThreadSession(session_id=_SESSION_ID, pool_id="discord:main:thread:123")
        ts = _make_turn_store()
        th = _make_thread_store(session=prior)
        cache: dict = {}
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="thread:123",
            platform_meta=DiscordMeta(channel_id=10, guild_id=50, thread_id=123),
        )
        ctx = _make_session_ctx(
            turn_store=ts, thread_store=th, thread_sessions_cache=cache
        )

        # Act
        result = await builder.build(msg, ctx)

        # Assert — thread_store.get_session called; cache populated; prior session set.
        # Negative: removing the `_discord_thread` path means thread_store is never
        # consulted and the thread's prior session_id is silently dropped.
        th.get_session.assert_awaited_once_with(thread_id="123", bot_id=_BOT_ID)
        ts.get_last_session.assert_not_awaited()
        assert "123" in cache
        assert result.session_update_fn is not None
        assert isinstance(result.platform_meta, DiscordMeta)
        assert result.platform_meta.thread_session_id == _SESSION_ID

        # Closure test: session_update_fn persists via thread_store.update_session.
        await result.session_update_fn(result, "sess-new-thread", "pool-th")
        th.update_session.assert_awaited_once_with(
            thread_id="123",
            bot_id=_BOT_ID,
            session_id="sess-new-thread",
            pool_id="pool-th",
        )
        ts.start_session.assert_not_awaited()
        # Cache updated to reflect new session.
        assert cache.get("123") == ThreadSession(
            session_id="sess-new-thread", pool_id="pool-th"
        )

    @pytest.mark.asyncio
    async def test_both_stores_discord_no_session_yet(self) -> None:
        # Arrange — thread_id present but retrieve returns unresolved ThreadSession.
        unresolved = ThreadSession(session_id=None, pool_id=None)
        ts = _make_turn_store()
        th = _make_thread_store(session=unresolved)
        cache: dict = {}
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="thread:456",
            platform_meta=DiscordMeta(channel_id=10, guild_id=50, thread_id=456),
        )
        ctx = _make_session_ctx(
            turn_store=ts, thread_store=th, thread_sessions_cache=cache
        )

        # Act — must not raise even though no prior session exists.
        result = await builder.build(msg, ctx)

        # Assert — session_update_fn attached; no prior session_id on meta; cache empty
        # (unresolved session not cached).
        # Negative: if the unresolved-session branch raised an exception, fresh-thread
        # messages would crash the inbound pipeline.
        th.get_session.assert_awaited_once_with(thread_id="456", bot_id=_BOT_ID)
        assert result.session_update_fn is not None
        assert isinstance(result.platform_meta, DiscordMeta)
        assert result.platform_meta.thread_session_id is None
        assert "456" not in cache

        # Closure still fires and persists the first session for this thread.
        await result.session_update_fn(result, "sess-first", "pool-first")
        th.update_session.assert_awaited_once_with(
            thread_id="456",
            bot_id=_BOT_ID,
            session_id="sess-first",
            pool_id="pool-first",
        )

    @pytest.mark.asyncio
    async def test_thread_update_fn_returns_early_when_thread_id_none(self) -> None:
        # Arrange — owned thread path; capture the session_update_fn closure.
        prior = ThreadSession(session_id=_SESSION_ID, pool_id="discord:main:thread:123")
        ts = _make_turn_store()
        th = _make_thread_store(session=prior)
        cache: dict = {}
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="thread:123",
            platform_meta=DiscordMeta(channel_id=10, guild_id=50, thread_id=123),
        )
        ctx = _make_session_ctx(
            turn_store=ts, thread_store=th, thread_sessions_cache=cache
        )
        result = await builder.build(msg, ctx)
        update_fn = result.session_update_fn
        assert update_fn is not None

        # Build an updated msg where thread_id=None so _tid evaluates to None inside
        # the closure.  Use dataclasses.replace to produce a new DiscordMeta.
        updated_meta = dataclasses.replace(result.platform_meta, thread_id=None)
        updated_msg = dataclasses.replace(result, platform_meta=updated_meta)

        # Reset the mock so only calls triggered by update_fn are counted.
        th.update_session.reset_mock()

        # Act
        await update_fn(updated_msg, "sess-irrelevant", "pool-irrelevant")

        # Assert — guard `if _tid is None: return` fired; thread_store never written.
        # Negative: deleting the `if _tid is None: return` guard in _thread_update_fn
        # makes _tid_str = str(None) = "None" and update_session IS called, failing
        # this assertion.
        th.update_session.assert_not_awaited()
