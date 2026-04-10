"""Integration test — Discord DM handler injects thread_session_id into inbound message.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.core.authenticator import _ALLOW_ALL

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTurnStore:
    """Returns a fixed prior session id for any pool_id."""

    def __init__(self, session_id: str = "prior-session-id") -> None:
        self._session_id = session_id

    async def get_last_session(self, pool_id: str) -> str | None:
        return self._session_id

    async def increment_resume_count(self, session_id: str) -> None:
        pass

    async def get_session_pool_id(self, session_id: str) -> str | None:
        return None

    async def log_turn(self, **_kwargs) -> None:
        pass


def _make_dm_message(channel_id: int = 555, user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        guild=None,  # DM — no guild
        channel=SimpleNamespace(id=channel_id, send=AsyncMock()),
        author=SimpleNamespace(
            id=user_id,
            name="Alice",
            display_name="Alice",
            bot=False,
        ),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=1000 + user_id,
        mentions=[],
        thread=None,
        attachments=[],
        message_thread_id=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_discord_dm_injects_thread_session_id() -> None:
    """DiscordAdapter with turn_store injects thread_session_id for DMs."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord_inbound import handle_message as discord_handle_message

    mock_bus = MagicMock()
    mock_bus.put_nowait = MagicMock()
    mock_bus.put = AsyncMock()
    fake_turn_store = _FakeTurnStore("prior-session-id")

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=mock_bus,
        intents=discord.Intents.none(),
        auth=_ALLOW_ALL,
        turn_store=fake_turn_store,  # type: ignore[arg-type]
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    fake_dm = _make_dm_message(channel_id=555, user_id=42)

    await discord_handle_message(adapter, fake_dm)

    # Verify something was posted to the bus
    assert mock_bus.put_nowait.called or mock_bus.put.called, (
        "No message was posted to the inbound bus"
    )

    # Extract the posted InboundMessage — put(platform, msg), msg is arg[1]
    if mock_bus.put_nowait.called:
        posted = mock_bus.put_nowait.call_args[0][1]
    else:
        posted = mock_bus.put.call_args[0][1]

    assert posted.platform_meta.get("thread_session_id") == "prior-session-id", (
        f"Expected thread_session_id='prior-session-id', "
        f"got platform_meta={posted.platform_meta!r}"
    )


async def test_discord_dm_no_turn_store_does_not_inject() -> None:
    """Without turn_store, thread_session_id should not be present in platform_meta.

    Verifies backward compatibility — adapters without turn_store still work and
    simply do not inject thread_session_id.
    """
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord_inbound import handle_message as discord_handle_message

    mock_bus = MagicMock()
    mock_bus.put_nowait = MagicMock()
    mock_bus.put = AsyncMock()

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=mock_bus,
        intents=discord.Intents.none(),
        auth=_ALLOW_ALL,
        # No turn_store
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    fake_dm = _make_dm_message(channel_id=555, user_id=42)

    await discord_handle_message(adapter, fake_dm)

    if mock_bus.put_nowait.called:
        posted = mock_bus.put_nowait.call_args[0][1]
        assert "thread_session_id" not in posted.platform_meta, (
            "thread_session_id must not appear in platform_meta without a turn_store"
        )


async def test_discord_dm_turn_store_attribute_stored() -> None:
    """DiscordAdapter must expose _turn_store after construction."""
    from lyra.adapters.discord import DiscordAdapter

    mock_bus = MagicMock()
    fake_turn_store = _FakeTurnStore("session-xyz")

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=mock_bus,
        intents=discord.Intents.none(),
        auth=_ALLOW_ALL,
        turn_store=fake_turn_store,  # type: ignore[arg-type]
    )

    assert adapter._turn_store is fake_turn_store, (
        "DiscordAdapter must store turn_store as _turn_store attribute"
    )
