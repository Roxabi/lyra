"""Tests for SessionBuilder.build — reduced 2-path session matrix (Wave 3, #1777).

Paths:
  (a) thread_store is None — test/CLI mode, msg returned unchanged.
  (d) thread_store present + Discord owned thread — claim-routing only;
      session_update_fn attached (no-op write-back); resume is hub-side path-3.

Removed paths (b, c) and removed symbols (LastSessionStore, ThreadSession,
thread_session_id, last_session, thread_sessions_cache, update_session) were
deleted in Wave 3 (#1777).
"""

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


def _make_thread_store() -> MagicMock:
    th = MagicMock()
    th.get_session = AsyncMock(return_value=None)
    th.update_session = AsyncMock(return_value=None)
    return th


def _make_session_ctx(
    *,
    turn_store: object = None,
    thread_store: object = None,
    turn_publisher: object = None,
) -> SessionCtx:
    return SessionCtx(
        turn_store=turn_store,  # type: ignore[arg-type]
        thread_store=thread_store,  # type: ignore[arg-type]
        turn_publisher=turn_publisher,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionBuilderPathA:
    """Path (a): thread_store None — test/CLI mode, msg returned unchanged."""

    @pytest.mark.asyncio
    async def test_thread_store_none_returns_unchanged(self) -> None:
        # Arrange
        builder = SessionBuilder()
        msg = _make_msg(platform_meta=TelegramMeta(chat_id=1))
        ctx = _make_session_ctx(turn_store=None, thread_store=None)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — Negative: removing the `if th is None: return msg` guard causes
        # the function to fall through to the discriminator block, which checks
        # isinstance(meta, DiscordMeta) — for Telegram msg this is False and the
        # function hits `return msg` at the end, so the behaviour is identical.
        # The real negative is that a Discord-thread msg with th=None would crash
        # (th.get_session on None) rather than return unchanged — covered below.
        assert result is msg
        assert result.session_update_fn is None

    @pytest.mark.asyncio
    async def test_telegram_thread_store_none_no_crash(self) -> None:
        """Telegram with thread_store=None: path (a) fires, no error."""
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            platform_meta=TelegramMeta(chat_id=5, is_group=True),
        )
        ctx = _make_session_ctx(thread_store=None)

        result = await builder.build(msg, ctx)

        assert result is msg
        assert result.session_update_fn is None

    @pytest.mark.asyncio
    async def test_discord_dm_thread_store_none_returns_unchanged(self) -> None:
        """Discord DM (thread_id=None) with thread_store=None: path (a) fires."""
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="channel:999",
            platform_meta=DiscordMeta(channel_id=999, guild_id=None, thread_id=None),
        )
        ctx = _make_session_ctx(thread_store=None)

        result = await builder.build(msg, ctx)

        assert result is msg
        assert result.session_update_fn is None


class TestSessionBuilderPathD:
    """Path (d): thread_store present + Discord owned thread — claim-routing only.

    _build_thread_path attaches a session_update_fn (no-op write-back).
    It does NOT call th.get_session, th.update_session, or set any session pointer
    on the message meta — resume is now hub-side path-3 (#1777).
    """

    @pytest.mark.asyncio
    async def test_discord_owned_thread_attaches_session_update_fn(self) -> None:
        """Discord thread (guild_id + thread_id present): session_update_fn attached.

        Negative: removing the `_discord_thread` discriminator makes build() fall
        through to `return msg` — session_update_fn would remain None and the
        turn handler would skip the claim-routing callback entirely.
        """
        # Arrange
        th = _make_thread_store()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="thread:123",
            platform_meta=DiscordMeta(channel_id=10, guild_id=50, thread_id=123),
        )
        ctx = _make_session_ctx(thread_store=th)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — claim-routing path: session_update_fn attached; no store reads.
        assert result is not msg  # new object via dataclasses.replace
        assert result.session_update_fn is not None
        # Thread-store NOT consulted — path-3 (hub-side) handles resume.
        th.get_session.assert_not_awaited()
        th.update_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discord_owned_thread_update_fn_is_callable(self) -> None:
        """session_update_fn closure can be called without raising."""
        # Arrange
        th = _make_thread_store()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="thread:456",
            platform_meta=DiscordMeta(channel_id=10, guild_id=50, thread_id=456),
        )
        ctx = _make_session_ctx(thread_store=th)
        result = await builder.build(msg, ctx)
        update_fn = result.session_update_fn
        assert update_fn is not None

        # Act — turn handler invocation must not raise.
        await update_fn(result, "sess-new", "discord:main:thread:456")

        # Assert — no store write (claim-routing only, write-back is hub-side).
        th.update_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discord_no_thread_id_returns_unchanged(self) -> None:
        """Discord with thread_id=None: _discord_thread guard is False → msg unchanged.

        Negative: removing the `meta.thread_id is not None` check in _discord_thread
        would route all Discord msgs (DMs included) into _build_thread_path, which
        asserts `meta.thread_id is not None` and would raise AssertionError.
        """
        # Arrange — Discord DM: guild_id set but thread_id absent
        th = _make_thread_store()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="channel:100",
            platform_meta=DiscordMeta(channel_id=100, guild_id=50, thread_id=None),
        )
        ctx = _make_session_ctx(thread_store=th)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — falls through to `return msg`; no session wiring.
        assert result is msg
        assert result.session_update_fn is None
        th.get_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_telegram_with_thread_store_returns_unchanged(self) -> None:
        """Telegram with thread_store: not DiscordMeta → falls through unchanged.

        Negative: if the isinstance(meta, DiscordMeta) check were removed,
        Telegram messages would also enter _build_thread_path which asserts
        isinstance(meta, DiscordMeta) and raise AssertionError.
        """
        # Arrange — Telegram msg with a thread_store in ctx
        th = _make_thread_store()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="telegram",
            platform_meta=TelegramMeta(chat_id=7),
        )
        ctx = _make_session_ctx(thread_store=th)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — discriminator skips Telegram; msg returned unchanged.
        assert result is msg
        assert result.session_update_fn is None
        th.get_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_discord_owned_thread_meta_preserved(self) -> None:
        """dataclasses.replace preserves all existing meta fields on the result."""
        # Arrange
        th = _make_thread_store()
        builder = SessionBuilder()
        original_meta = DiscordMeta(
            channel_id=10, guild_id=50, thread_id=123, channel_type="public_thread"
        )
        msg = _make_msg(
            platform="discord",
            scope_id="thread:123",
            platform_meta=original_meta,
        )
        ctx = _make_session_ctx(thread_store=th)

        # Act
        result = await builder.build(msg, ctx)

        # Assert — meta fields unchanged (only session_update_fn is new).
        assert result.platform_meta is original_meta
        assert result.platform == msg.platform
        assert result.bot_id == msg.bot_id
        assert result.scope_id == msg.scope_id

    @pytest.mark.asyncio
    async def test_update_fn_no_op_with_none_thread_id(self) -> None:
        """Closure is safe even when called with an updated msg where thread_id=None.

        The closure captures _th and _bid by value. If the caller passes a msg
        whose DiscordMeta has thread_id=None, the closure must not raise.
        """
        # Arrange
        th = _make_thread_store()
        builder = SessionBuilder()
        msg = _make_msg(
            platform="discord",
            scope_id="thread:789",
            platform_meta=DiscordMeta(channel_id=10, guild_id=50, thread_id=789),
        )
        ctx = _make_session_ctx(thread_store=th)
        result = await builder.build(msg, ctx)
        update_fn = result.session_update_fn
        assert update_fn is not None

        # Build a msg variant with thread_id=None (edge case for closure safety).
        updated_meta = dataclasses.replace(result.platform_meta, thread_id=None)
        updated_msg = dataclasses.replace(result, platform_meta=updated_meta)

        # Act — must not raise.
        await update_fn(updated_msg, "sess-x", "pool-x")

        # Assert — no store write regardless.
        th.update_session.assert_not_awaited()
