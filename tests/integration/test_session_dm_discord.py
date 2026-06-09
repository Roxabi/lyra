"""Integration test — Discord DM handler injects thread_session_id
into inbound message.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from factory.adapters.discord import DiscordAdapter
    from factory.core.messaging.bus import Bus
    from factory.core.messaging.message import InboundMessage

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from factory.core.ports.last_session_store import LastSessionStore

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

    async def set_last_session(self, pool_id: str, session_id: str) -> None:
        pass

    async def increment_resume_count(self, session_id: str) -> None:
        pass

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


def _make_discord_adapter(
    bot_id: str = "main",
    inbound_bus: "Bus[InboundMessage] | None" = None,
    last_session: "LastSessionStore | None" = None,
) -> "DiscordAdapter":
    """Build a DiscordAdapter with optional last_session injection."""
    from factory.adapters.discord import DiscordAdapter

    mock_bus = inbound_bus if inbound_bus is not None else MagicMock()

    return DiscordAdapter(
        bot_id=bot_id,
        inbound_bus=mock_bus,
        intents=discord.Intents.none(),
        last_session=last_session,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_discord_dm_injects_thread_session_id() -> None:
    """DiscordAdapter with turn_store injects thread_session_id for DMs."""
    from factory.adapters.discord.discord_inbound import (
        handle_message as discord_handle_message,
    )

    mock_bus = MagicMock()
    mock_bus.put_nowait = MagicMock()
    mock_bus.put = AsyncMock()
    fake_turn_store = _FakeTurnStore("prior-session-id")

    adapter = _make_discord_adapter(
        inbound_bus=mock_bus,
        last_session=cast("LastSessionStore", fake_turn_store),
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

    from factory.core.messaging.message import DiscordMeta

    assert isinstance(posted.platform_meta, DiscordMeta), (
        f"Expected DiscordMeta, got {posted.platform_meta!r}"
    )
    assert posted.platform_meta.thread_session_id == "prior-session-id", (
        f"Expected thread_session_id='prior-session-id', "
        f"got platform_meta={posted.platform_meta!r}"
    )


async def test_discord_dm_no_turn_store_does_not_inject() -> None:
    """Without turn_store, thread_session_id should not be present in platform_meta.

    Verifies backward compatibility — adapters without turn_store still work and
    simply do not inject thread_session_id.
    """
    from factory.adapters.discord.discord_inbound import (
        handle_message as discord_handle_message,
    )

    mock_bus = MagicMock()
    mock_bus.put_nowait = MagicMock()
    mock_bus.put = AsyncMock()

    adapter = _make_discord_adapter(inbound_bus=mock_bus)
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    fake_dm = _make_dm_message(channel_id=555, user_id=42)

    await discord_handle_message(adapter, fake_dm)

    # Unconditional: a message must have been dispatched (vacuous guard removed)
    assert mock_bus.put_nowait.called or mock_bus.put.called, (
        "message must be dispatched"
    )

    # Extract the posted InboundMessage — handle both put_nowait and put paths
    if mock_bus.put_nowait.called:
        posted = mock_bus.put_nowait.call_args[0][1]
    else:
        posted = mock_bus.put.call_args[0][1]

    from factory.core.messaging.message import DiscordMeta

    assert isinstance(posted.platform_meta, DiscordMeta), (
        f"Expected DiscordMeta, got {posted.platform_meta!r}"
    )
    assert posted.platform_meta.thread_session_id is None, (
        "thread_session_id must be None in platform_meta without a turn_store, "
        f"got platform_meta={posted.platform_meta!r}"
    )


async def test_discord_dm_turn_store_attribute_stored() -> None:
    """DiscordAdapter must expose _turn_store after construction."""
    fake_turn_store = _FakeTurnStore("session-xyz")

    adapter = _make_discord_adapter(
        last_session=cast("LastSessionStore", fake_turn_store),
    )

    assert adapter._last_session is fake_turn_store, (
        "DiscordAdapter must store last_session as _last_session attribute"
    )
