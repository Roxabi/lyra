"""Tests for DiscordAdapter auth gate (S5)."""

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
    """Build a minimal discord-like DM message SimpleNamespace.

    Uses guild=None (DM) so messages pass the group-chat filter introduced
    in 9f9072d. Tests that specifically need guild context should set guild
    directly on the returned namespace.
    """
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
# Slice S5: DiscordAdapter auth gate tests
# ---------------------------------------------------------------------------


class TestDiscordAuth:
    """Auth gate tests for DiscordAdapter.on_message."""

    @pytest.mark.asyncio
    async def test_blocked_user_skips_normalize(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """BLOCKED user: on_message returns early without calling normalize()."""
        import logging
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.BLOCKED
        auth.resolve.return_value = Identity(
            user_id="dc:user:42",
            trust_level=TrustLevel.BLOCKED,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        with caplog.at_level(logging.INFO, logger="lyra.adapters.discord"):
            with patch.object(adapter, "normalize") as mock_norm:
                await adapter.on_message(_make_discord_msg_ns())

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()
        assert any("auth_reject" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_role_match_returns_trust(self) -> None:
        """User with a matching role: message produced with correct trust_level."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.TRUSTED
        auth.resolve.return_value = Identity(
            user_id="dc:user:42",
            trust_level=TrustLevel.TRUSTED,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        msg_ns = _make_discord_msg_ns(roles=["123456"])
        await adapter.on_message(msg_ns)

        # Verify role snowflake IDs were passed to auth.resolve
        call_kwargs = auth.resolve.call_args
        assert call_kwargs is not None
        passed_roles = call_kwargs.kwargs.get("roles") or (
            call_kwargs.args[1] if len(call_kwargs.args) > 1 else []
        )
        assert "123456" in passed_roles

    @pytest.mark.asyncio
    async def test_dm_fallback_user_id_only(self) -> None:
        """DM message (no roles attribute): auth.check called with roles=[]."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.PUBLIC
        auth.resolve.return_value = Identity(
            user_id="dc:user:42",
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        # No roles attribute on author (DM scenario)
        dm_msg = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="hello",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
            reply=AsyncMock(),
            attachments=[],
        )

        await adapter.on_message(dm_msg)

        call_kwargs = auth.resolve.call_args
        assert call_kwargs is not None
        passed_roles = call_kwargs.kwargs.get("roles") or (
            call_kwargs.args[1] if len(call_kwargs.args) > 1 else []
        )
        assert passed_roles == []

    @pytest.mark.asyncio
    async def test_public_user_message_forwarded(self) -> None:
        """PUBLIC user: message reaches bus with trust_level=TrustLevel.PUBLIC."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.PUBLIC
        auth.resolve.return_value = Identity(
            user_id="dc:user:42",
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        msg_ns = _make_discord_msg_ns()
        await adapter.on_message(msg_ns)

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC
        assert msg.is_admin is False

    @pytest.mark.asyncio
    async def test_admin_user_has_is_admin_set(self) -> None:
        """Admin user: message produced with is_admin=True."""
        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.resolve.return_value = Identity(
            user_id="dc:user:42",
            trust_level=TrustLevel.TRUSTED,
            is_admin=True,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)

        msg_ns = _make_discord_msg_ns()
        await adapter.on_message(msg_ns)

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.is_admin is True

    @pytest.mark.asyncio
    async def test_integration_blocked_user_rejected_by_real_guard(self) -> None:
        """Integration: real Authenticator + real GuardChain rejects BLOCKED user."""
        from unittest.mock import patch

        from lyra.adapters.discord import DiscordAdapter
        from lyra.core.authenticator import Authenticator
        from lyra.core.guard import BlockedGuard, GuardChain

        store = MagicMock()
        store.check.return_value = TrustLevel.BLOCKED
        auth = Authenticator(store=store, role_map={}, default=TrustLevel.BLOCKED)
        guard_chain = GuardChain([BlockedGuard()])

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = DiscordAdapter(
            hub=hub, bot_id="main", intents=discord.Intents.none(), auth=auth
        )
        adapter._bot_user = SimpleNamespace(id=999, bot=True)
        # Inject real guard chain
        adapter._guard_chain = guard_chain

        with patch.object(adapter, "normalize") as mock_norm:
            await adapter.on_message(_make_discord_msg_ns())

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()
