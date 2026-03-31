"""Tests for Discord adapter inbound path and Hub-side auth gate (C3).

After C3 (trust re-resolution #456), adapters forward all messages with
trust_level=PUBLIC to the bus; the Hub resolves trust and TrustGuardMiddleware
drops BLOCKED users. These tests verify the adapter-side half of that contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _make_discord_msg_ns(user_id: int = 42, roles: list | None = None) -> object:
    """Build a minimal discord-like DM message SimpleNamespace."""
    author_kwargs: dict = {
        "id": user_id,
        "name": "Alice",
        "display_name": "Alice",
        "bot": False,
    }
    if roles is not None:
        author_kwargs["roles"] = [SimpleNamespace(id=r) for r in roles]
    return SimpleNamespace(
        guild=None,  # DM context — passes group-chat filter
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(**author_kwargs),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=AsyncMock(),
        attachments=[],
    )


# ---------------------------------------------------------------------------
# C3: Adapter always forwards with PUBLIC trust — Hub resolves trust
# ---------------------------------------------------------------------------


class TestDiscordAdapterInbound:
    """C3 contract: adapter forwards all non-bot messages with raw PUBLIC trust."""

    @pytest.mark.asyncio
    async def test_any_user_forwarded_with_public_trust(self) -> None:
        """All users reach the bus with trust_level=PUBLIC (Hub resolves trust)."""
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = AsyncMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        await adapter.on_message(_make_discord_msg_ns())

        hub.inbound_bus.put.assert_awaited_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC
        assert msg.is_admin is False

    @pytest.mark.asyncio
    async def test_bot_message_still_dropped_early(self) -> None:
        """Bot-authored messages are filtered before reaching the bus."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        bot_msg = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(id=99, name="Bot", display_name="Bot", bot=True),
            content="I'm a bot",
            created_at=datetime.now(timezone.utc),
            id=556,
            mentions=[],
            reply=AsyncMock(),
            attachments=[],
        )

        with patch.object(adapter, "normalize") as mock_norm:
            await adapter.on_message(bot_msg)

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_with_roles_forwarded_with_public_trust(self) -> None:
        """User with roles is forwarded with PUBLIC trust (roles irrelevant at adapter)."""  # noqa: E501
        from lyra.adapters.discord import DiscordAdapter

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = AsyncMock()
        adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        msg_ns = _make_discord_msg_ns(roles=["123456"])
        await adapter.on_message(msg_ns)

        hub.inbound_bus.put.assert_awaited_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC


# ---------------------------------------------------------------------------
# C3: Hub-side trust resolution
# ---------------------------------------------------------------------------


class TestHubTrustResolution:
    """Hub._resolve_message_trust() correctly overwrites adapter-supplied trust."""

    def test_resolves_trust_from_authenticator(self) -> None:
        """Hub re-resolves trust on dequeued message."""
        from lyra.core.authenticator import Authenticator
        from lyra.core.hub.hub import Hub
        from lyra.core.message import InboundMessage, Platform

        store = MagicMock()
        store.check.return_value = TrustLevel.TRUSTED
        auth = Authenticator(store=store, role_map={}, default=TrustLevel.PUBLIC)

        hub = Hub()
        hub.register_authenticator(Platform.TELEGRAM, "main", auth)

        msg = InboundMessage(
            id="test-1",
            platform="telegram",
            bot_id="main",
            scope_id="tg:user:42",
            user_id="tg:user:42",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            trust_level=TrustLevel.PUBLIC,  # adapter-supplied raw value
            is_admin=False,
        )

        result = hub._resolve_message_trust(msg)

        assert result.trust_level == TrustLevel.TRUSTED

    def test_no_authenticator_returns_message_unchanged(self) -> None:
        """Hub returns message unchanged when no authenticator registered."""
        from lyra.core.hub.hub import Hub
        from lyra.core.message import InboundMessage

        hub = Hub()

        msg = InboundMessage(
            id="test-2",
            platform="telegram",
            bot_id="main",
            scope_id="tg:user:42",
            user_id="tg:user:42",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )

        result = hub._resolve_message_trust(msg)

        assert result is msg  # unchanged — same object


# ---------------------------------------------------------------------------
# C3: TrustGuardMiddleware drops BLOCKED users
# ---------------------------------------------------------------------------


class TestTrustGuardMiddleware:
    """TrustGuardMiddleware drops messages from BLOCKED users."""

    @pytest.mark.asyncio
    async def test_blocked_message_dropped(self) -> None:
        """Message with BLOCKED trust level is dropped; next() not called."""
        from unittest.mock import AsyncMock

        from lyra.core.hub.message_pipeline import _DROP
        from lyra.core.hub.middleware import PipelineContext
        from lyra.core.hub.middleware_stages import TrustGuardMiddleware
        from lyra.core.message import InboundMessage

        mw = TrustGuardMiddleware()
        next_fn = AsyncMock()
        ctx = MagicMock(spec=PipelineContext)
        ctx.emit = MagicMock()

        msg = MagicMock(spec=InboundMessage)
        msg.trust_level = TrustLevel.BLOCKED
        msg.user_id = "tg:user:42"
        msg.platform = "telegram"
        msg.id = "test-id"

        result = await mw(msg, ctx, next_fn)

        assert result is _DROP
        next_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_blocked_message_passes_through(self) -> None:
        """Message with PUBLIC/TRUSTED trust level passes to next middleware."""
        from unittest.mock import AsyncMock

        from lyra.core.hub.middleware import PipelineContext
        from lyra.core.hub.middleware_stages import TrustGuardMiddleware
        from lyra.core.message import InboundMessage

        mw = TrustGuardMiddleware()
        sentinel = object()
        next_fn = AsyncMock(return_value=sentinel)
        ctx = MagicMock(spec=PipelineContext)
        ctx.emit = MagicMock()

        for trust in (TrustLevel.PUBLIC, TrustLevel.TRUSTED, TrustLevel.OWNER):
            msg = MagicMock(spec=InboundMessage)
            msg.trust_level = trust
            msg.user_id = "tg:user:42"
            msg.platform = "telegram"
            msg.id = f"test-id-{trust}"

            result = await mw(msg, ctx, next_fn)

            assert result is sentinel, f"Expected pass-through for trust={trust}"
