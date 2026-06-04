"""Tests for SessionBuilder.build — 4-path session-injection matrix."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import (
    DiscordMeta,
    InboundMessage,
    PlatformMeta,
    TelegramMeta,
)
from factory.core.stores.thread_store_protocol import ThreadSession
from factory.inbound.context import SessionCtx
from factory.inbound.session_builder import SessionBuilder

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


def _make_turn_publisher() -> MagicMock:
    pub = MagicMock()
    pub.publish_start_session = AsyncMock(return_value=None)
    return pub


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
    turn_publisher: object = None,
    last_session: object = None,
) -> SessionCtx:
    cache: dict = thread_sessions_cache if thread_sessions_cache is not None else {}
    return SessionCtx(
        turn_store=turn_store,  # type: ignore[arg-type]
        thread_store=thread_store,  # type: ignore[arg-type]
        thread_sessions_cache=cache,
        turn_publisher=turn_publisher,  # type: ignore[arg-type]
        last_session=last_session,  # type: ignore[arg-type]
    )


class _FakeLastSession:
    """Minimal in-memory LastSessionStore for path B/C tests."""

    def __init__(self, prior: str | None = None) -> None:
        self._prior = prior
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, str]] = []

    async def get_last_session(self, pool_id: str) -> str | None:
        self.get_calls.append(pool_id)
        return self._prior

    async def set_last_session(self, pool_id: str, session_id: str) -> None:
        self.set_calls.append((pool_id, session_id))


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
    """Path (b): last_session port — Telegram DM.

    D9: SessionBuilder reads last-session ONLY via ctx.last_session.get_last_session.
    The turn_store is inert for last-session reads; ts.get_last_session is never called.
    """

    @pytest.mark.asyncio
    async def test_telegram_dm_reads_prior_session_from_last_session_port(
        self,
    ) -> None:
        """last_session port consulted; prior session propagated to TelegramMeta.

        Negative: removing the ctx.last_session.get_last_session call in
        _build_turnstore_path means thread_session_id is never populated and
        resume-session is silently broken.
        """
        # Arrange
        ls = _FakeLastSession(prior=_SESSION_ID)
        pub = _make_turn_publisher()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            platform_meta=TelegramMeta(chat_id=1, is_group=False),
        )
        ctx = _make_session_ctx(
            turn_store=None, thread_store=None, turn_publisher=pub, last_session=ls
        )

        # Act
        result = await builder.build(msg, ctx)

        # Assert — last_session port consulted; session_update_fn attached
        assert ls.get_calls, "last_session.get_last_session was never called"
        assert result.session_update_fn is not None
        # prior session_id propagated onto TelegramMeta.thread_session_id
        assert isinstance(result.platform_meta, TelegramMeta)
        assert result.platform_meta.thread_session_id == _SESSION_ID

        # Closure test: calling session_update_fn triggers publish_start_session.
        await result.session_update_fn(result, "new-sess", _POOL_ID)
        pub.publish_start_session.assert_called_once()
        _, kwargs = pub.publish_start_session.call_args
        assert kwargs["session_id"] == "new-sess"
        assert kwargs["pool_id"] == _POOL_ID
        assert kwargs["trace_id"]  # non-empty

    @pytest.mark.asyncio
    async def test_telegram_group_mention_reads_via_last_session_port(self) -> None:
        """Group mention: last_session port consulted regardless of is_group/is_mention.

        Negative: if SessionBuilder branched on is_group, group messages would skip
        session wiring and the turn handler would call a None session_update_fn.
        """
        # Arrange — no prior session
        ls = _FakeLastSession(prior=None)
        pub = _make_turn_publisher()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            is_mention=True,
            platform_meta=TelegramMeta(chat_id=5, is_group=True),
        )
        ctx = _make_session_ctx(
            turn_store=None, thread_store=None, turn_publisher=pub, last_session=ls
        )

        # Act
        result = await builder.build(msg, ctx)

        # Assert — session_update_fn still attached; no prior session_id (last=None).
        assert ls.get_calls, "last_session.get_last_session was never called"
        assert result.session_update_fn is not None
        # No prior session → thread_session_id not written
        assert isinstance(result.platform_meta, TelegramMeta)
        assert result.platform_meta.thread_session_id is None

        # Closure still fires via publisher.
        await result.session_update_fn(result, "sess-new", "pool-2")
        pub.publish_start_session.assert_called_once()
        _, kwargs = pub.publish_start_session.call_args
        assert kwargs["session_id"] == "sess-new"
        assert kwargs["pool_id"] == "pool-2"


class TestSessionBuilderPathC:
    """Path (c): Discord DM — last_session port read, thread_store skipped.

    D9: last_session port is the sole read path; turn_store is inert for reads.
    """

    @pytest.mark.asyncio
    async def test_discord_dm_reads_last_session_port_skips_thread_store(
        self,
    ) -> None:
        """Discord DM: last_session port consulted; thread_store NOT consulted.

        Negative: removing the `_discord_thread` guard (thread_id is None check)
        would route through _build_thread_path, calling th.get_session instead.
        """
        # Arrange — Discord DM: guild_id=None, thread_id=None
        ls = _FakeLastSession(prior=_SESSION_ID)
        th = _make_thread_store()
        pub = _make_turn_publisher()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="channel:999",
            platform_meta=DiscordMeta(channel_id=999, guild_id=None, thread_id=None),
        )
        ctx = _make_session_ctx(
            turn_store=None, thread_store=th, turn_publisher=pub, last_session=ls
        )

        # Act
        result = await builder.build(msg, ctx)

        # Assert — last_session port consulted; thread_store NOT consulted
        assert ls.get_calls, "last_session.get_last_session was never called"
        th.get_session.assert_not_awaited()
        assert result.session_update_fn is not None

        # Closure publishes via publisher, not thread_store.
        await result.session_update_fn(result, "sess-dm", "pool-dm")
        pub.publish_start_session.assert_called_once()
        _, kwargs = pub.publish_start_session.call_args
        assert kwargs["session_id"] == "sess-dm"
        assert kwargs["pool_id"] == "pool-dm"
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


# ---------------------------------------------------------------------------
# T21 — SessionBuilder × LastSessionStore port (#1721)
# ---------------------------------------------------------------------------


class _InMemoryLastSessionStore:
    """Simple in-memory LastSessionStore for injection tests."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []
        self.get_calls: list[str] = []

    async def get_last_session(self, pool_id: str) -> str | None:
        self.get_calls.append(pool_id)
        return self._store.get(pool_id)

    async def set_last_session(self, pool_id: str, session_id: str) -> None:
        self.set_calls.append((pool_id, session_id))
        self._store[pool_id] = session_id


def _make_session_ctx_with_last_session(
    *,
    turn_store: object = None,
    thread_store: object = None,
    last_session: object = None,
    turn_publisher: object = None,
    thread_sessions_cache: dict | None = None,
) -> SessionCtx:
    cache: dict = thread_sessions_cache if thread_sessions_cache is not None else {}
    return SessionCtx(
        turn_store=turn_store,  # type: ignore[arg-type]
        thread_store=thread_store,  # type: ignore[arg-type]
        thread_sessions_cache=cache,
        turn_publisher=turn_publisher,  # type: ignore[arg-type]
        last_session=last_session,  # type: ignore[arg-type]
    )


class TestSessionBuilderLastSessionPort:
    """T21: SessionBuilder reads/writes via ctx.last_session port."""

    @pytest.mark.asyncio
    async def test_builder_reads_prior_session_via_last_session_port(self) -> None:
        """builder.build() calls last_session.get_last_session for prior session_id.

        Negative: if SessionBuilder reads ctx.turn_store.get_last_session instead of
        ctx.last_session.get_last_session, the KV-backed path is silently bypassed and
        the feature is never exercised (tautological test without this check).
        """
        # Arrange
        ls = _InMemoryLastSessionStore()
        ls._store["telegram:main:chat:123"] = "sess-prior"
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            platform_meta=TelegramMeta(chat_id=1),
        )
        ctx = _make_session_ctx_with_last_session(
            turn_store=None,
            last_session=ls,
        )

        # Act
        result = await builder.build(msg, ctx)

        # Assert — get_last_session was called with the correct pool_id
        assert ls.get_calls, "get_last_session was never called — port not used"
        # prior session_id propagated to platform_meta.thread_session_id
        assert isinstance(result.platform_meta, TelegramMeta)
        assert result.platform_meta.thread_session_id == "sess-prior"

    @pytest.mark.asyncio
    async def test_builder_degrades_to_new_session_when_last_session_is_none(
        self,
    ) -> None:
        """last_session=None in ctx → msg returned with session_update_fn (new session).

        When both turn_store and last_session are None, path (a) returns unchanged msg.
        When only last_session is None but turn_store present, it degrades gracefully.
        """
        # Arrange — last_session=None, no turn_store either → path (a)
        builder = SessionBuilder()
        msg = _make_msg(platform="telegram", platform_meta=TelegramMeta(chat_id=1))
        ctx = _make_session_ctx_with_last_session(turn_store=None, last_session=None)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — path (a): msg unchanged, no crash
        assert result is msg
        assert result.session_update_fn is None

    @pytest.mark.asyncio
    async def test_builder_degrades_when_get_last_session_returns_none(self) -> None:
        """get_last_session returning None → new session, no crash, update_fn set."""
        # Arrange — last_session store with no prior entry
        ls = _InMemoryLastSessionStore()
        builder = SessionBuilder()
        msg = _make_msg(platform="telegram", platform_meta=TelegramMeta(chat_id=1))
        ctx = _make_session_ctx_with_last_session(
            turn_store=None,
            last_session=ls,
        )

        # Act
        result = await builder.build(msg, ctx)

        # Assert — no crash; session_update_fn attached; no thread_session_id set
        assert ls.get_calls, "get_last_session was never called"
        assert result.session_update_fn is not None
        assert isinstance(result.platform_meta, TelegramMeta)
        assert result.platform_meta.thread_session_id is None

    @pytest.mark.asyncio
    async def test_set_last_session_called_in_update_fn(self) -> None:
        """_turnstore_update_fn calls last_session.set_last_session after assign.

        Negative: if the update closure omits the set_last_session call, KV is never
        written and all future messages start a new session (silent regression).
        """
        # Arrange
        ls = _InMemoryLastSessionStore()
        pub = _make_turn_publisher()
        builder = SessionBuilder()
        msg = _make_msg(platform="telegram", platform_meta=TelegramMeta(chat_id=1))
        ctx = _make_session_ctx_with_last_session(
            turn_store=None,
            last_session=ls,
            turn_publisher=pub,
        )

        # Act — build to get the closure
        result = await builder.build(msg, ctx)
        assert result.session_update_fn is not None

        # Invoke the closure as the turn handler would
        await result.session_update_fn(result, "sess-new", "telegram:main:chat:123")

        # Assert — set_last_session was called with the new session_id
        assert ("telegram:main:chat:123", "sess-new") in ls.set_calls, (
            "set_last_session was never called in update closure — KV write missing"
        )

    @pytest.mark.asyncio
    async def test_set_last_session_not_called_when_last_session_is_none(
        self,
    ) -> None:
        """update closure skips set_last_session when last_session is None.

        Negative: if the closure always calls set_last_session regardless of None,
        AttributeError crashes the update path for hub-side wiring (TurnStoreLastSession
        set=no-op is correct, but None would crash).
        """
        # Arrange — turn_store only (hub path), no last_session
        ts = _make_turn_store(last_session=None)
        pub = _make_turn_publisher()
        builder = SessionBuilder()
        msg = _make_msg(platform="telegram", platform_meta=TelegramMeta(chat_id=1))
        ctx = _make_session_ctx_with_last_session(
            turn_store=ts,
            last_session=None,
            turn_publisher=pub,
        )

        # Act
        result = await builder.build(msg, ctx)
        assert result.session_update_fn is not None

        # Invoke closure — must not raise even with last_session=None
        await result.session_update_fn(result, "sess-hub", "telegram:main:chat:123")
