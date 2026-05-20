"""Tests for Router.decide — pure routing decision (PROCESS / DROP)."""

from __future__ import annotations

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    DiscordMeta,
    InboundMessage,
    PlatformMeta,
    TelegramMeta,
)
from lyra.inbound.context import RouterCtx
from lyra.inbound.router import RouteDecision, Router

# -- Helpers -----------------------------------------------------------------

_BOT_ID = "bot-1"
_GUILD_ID = 100
_OWNED_THREAD_ID = 123
_WATCH_CHANNEL_ID = 789


def _make_msg(
    *,
    platform: str = "telegram",
    is_mention: bool = False,
    platform_meta: PlatformMeta | None = None,
) -> InboundMessage:
    if platform_meta is None:
        platform_meta = TelegramMeta()
    return InboundMessage(
        id="msg-1",
        platform=platform,
        bot_id=_BOT_ID,
        scope_id="chat:123",
        user_id="user:42",
        user_name="testuser",
        is_mention=is_mention,
        text="hello",
        text_raw="hello",
        trust_level=TrustLevel.PUBLIC,
        platform_meta=platform_meta,
    )


def _tg_meta(*, is_group: bool) -> TelegramMeta:
    return TelegramMeta(chat_id=1, is_group=is_group)


def _discord_meta(
    *,
    guild_id: int | None = None,
    thread_id: int | None = None,
    channel_id: int = 1,
) -> DiscordMeta:
    return DiscordMeta(channel_id=channel_id, guild_id=guild_id, thread_id=thread_id)


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def router() -> Router:
    return Router()


@pytest.fixture
def router_ctx_discord() -> RouterCtx:
    return RouterCtx(
        bot_id=_BOT_ID,
        owned_threads={_OWNED_THREAD_ID, 456},
        watch_channels=frozenset({_WATCH_CHANNEL_ID}),
    )


@pytest.fixture
def router_ctx_telegram() -> RouterCtx:
    return RouterCtx(
        bot_id=_BOT_ID,
        owned_threads=set(),
        watch_channels=None,
    )


# -- Telegram ----------------------------------------------------------------


class TestRouterTelegram:
    """Router.decide — Telegram platform routing rules."""

    def test_dm_is_processed(
        self, router: Router, router_ctx_telegram: RouterCtx
    ) -> None:
        # Arrange — DM: is_group=False, no mention required
        msg = _make_msg(
            platform="telegram",
            is_mention=False,
            platform_meta=_tg_meta(is_group=False),
        )
        # Act
        decision = router.decide(msg, router_ctx_telegram)
        # Assert — Negative: removing the `not meta.is_group` guard causes DROP
        assert decision is RouteDecision.PROCESS

    def test_group_with_mention_is_processed(
        self, router: Router, router_ctx_telegram: RouterCtx
    ) -> None:
        # Arrange — group + direct mention
        msg = _make_msg(
            platform="telegram",
            is_mention=True,
            platform_meta=_tg_meta(is_group=True),
        )
        # Act
        decision = router.decide(msg, router_ctx_telegram)
        # Assert — Negative: removing the `msg.is_mention` guard causes DROP
        assert decision is RouteDecision.PROCESS

    def test_group_without_mention_is_dropped(
        self, router: Router, router_ctx_telegram: RouterCtx
    ) -> None:
        # Arrange — group chat, no mention
        msg = _make_msg(
            platform="telegram",
            is_mention=False,
            platform_meta=_tg_meta(is_group=True),
        )
        # Act
        decision = router.decide(msg, router_ctx_telegram)
        # Assert — Negative: if group guard is deleted, DMs would still pass but
        # group+no-mention incorrectly processes instead of drops
        assert decision is RouteDecision.DROP

    def test_group_bot_mention_via_is_mention_is_processed(
        self, router: Router, router_ctx_telegram: RouterCtx
    ) -> None:
        # Arrange — group + bot explicitly mentioned (is_mention=True)
        msg = _make_msg(
            platform="telegram",
            is_mention=True,
            platform_meta=_tg_meta(is_group=True),
        )
        # Act
        decision = router.decide(msg, router_ctx_telegram)
        # Assert
        assert decision is RouteDecision.PROCESS


# -- Discord -----------------------------------------------------------------


class TestRouterDiscord:
    """Router.decide — Discord platform routing rules."""

    def test_dm_no_guild_no_thread_is_processed(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — plain DM: guild_id=None, thread_id=None
        msg = _make_msg(
            platform="discord",
            is_mention=False,
            platform_meta=_discord_meta(guild_id=None, thread_id=None),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert — Negative: removing the `is_dm` guard causes DROP
        assert decision is RouteDecision.PROCESS

    def test_mention_outside_thread_is_processed(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — server channel, no thread, direct mention
        msg = _make_msg(
            platform="discord",
            is_mention=True,
            platform_meta=_discord_meta(guild_id=_GUILD_ID, thread_id=None),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert — Negative: removing `msg.is_mention` guard causes DROP
        assert decision is RouteDecision.PROCESS

    def test_mention_in_thread_is_processed(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — owned thread + mention
        msg = _make_msg(
            platform="discord",
            is_mention=True,
            platform_meta=_discord_meta(guild_id=_GUILD_ID, thread_id=_OWNED_THREAD_ID),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert
        assert decision is RouteDecision.PROCESS

    def test_owned_thread_no_mention_is_processed(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — owned thread, no mention (thread_id in ctx.owned_threads)
        msg = _make_msg(
            platform="discord",
            is_mention=False,
            platform_meta=_discord_meta(guild_id=_GUILD_ID, thread_id=_OWNED_THREAD_ID),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert — Negative: removing `thread_id in ctx.owned_threads` guard causes DROP
        assert decision is RouteDecision.PROCESS

    def test_thread_not_owned_no_mention_is_dropped(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — thread_id=999 NOT in owned_threads={123, 456}
        msg = _make_msg(
            platform="discord",
            is_mention=False,
            platform_meta=_discord_meta(guild_id=_GUILD_ID, thread_id=999),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert — Negative: if owned_threads lookup is removed, 999 wrongly processes
        assert decision is RouteDecision.DROP

    def test_watch_channel_no_mention_is_processed(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — channel_id in watch_channels, no thread, no mention
        msg = _make_msg(
            platform="discord",
            is_mention=False,
            platform_meta=_discord_meta(
                guild_id=_GUILD_ID, thread_id=None, channel_id=_WATCH_CHANNEL_ID
            ),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert — Negative: removing `channel_id in ctx.watch_channels` causes DROP
        assert decision is RouteDecision.PROCESS

    def test_regular_channel_no_mention_is_dropped(
        self, router: Router, router_ctx_discord: RouterCtx
    ) -> None:
        # Arrange — channel_id=1 NOT in watch_channels={789}, no thread, no mention
        msg = _make_msg(
            platform="discord",
            is_mention=False,
            platform_meta=_discord_meta(
                guild_id=_GUILD_ID, thread_id=None, channel_id=1
            ),
        )
        # Act
        decision = router.decide(msg, router_ctx_discord)
        # Assert — no pass condition reached: all guards fail
        assert decision is RouteDecision.DROP

    def test_watch_channels_none_regular_channel_is_dropped(
        self, router: Router
    ) -> None:
        # Arrange — watch_channels=None (not configured), regular channel
        ctx = RouterCtx(
            bot_id=_BOT_ID,
            owned_threads=set(),
            watch_channels=None,
        )
        msg = _make_msg(
            platform="discord",
            is_mention=False,
            platform_meta=_discord_meta(
                guild_id=_GUILD_ID, thread_id=None, channel_id=1
            ),
        )
        # Act
        decision = router.decide(msg, ctx)
        # Assert — None watch_channels skips the guard, falls through to DROP
        assert decision is RouteDecision.DROP


# -- Unknown platform --------------------------------------------------------


class TestRouterUnknownPlatform:
    """Router.decide — conservative DROP for unknown PlatformMeta subclasses."""

    def test_unknown_meta_is_dropped(
        self, router: Router, router_ctx_telegram: RouterCtx
    ) -> None:
        # Arrange — GenericMeta (not TelegramMeta or DiscordMeta)
        from lyra.core.messaging.message import GenericMeta

        msg = _make_msg(
            platform="unknown",
            is_mention=True,
            platform_meta=GenericMeta(),
        )
        # Act
        decision = router.decide(msg, router_ctx_telegram)
        # Assert — Negative: deleting the final fallback `return DROP` means no
        # branch returns for GenericMeta; the function would return None implicitly.
        assert decision is RouteDecision.DROP
