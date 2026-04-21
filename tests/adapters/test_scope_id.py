"""Unit tests — scope_id must NOT be user-scoped in shared spaces.

Issue #592: Groups / guild channels must produce the same scope_id for all
users so everyone shares one pool.  Currently both adapters add per-user
suffixes, causing each user to get their own pool.

RED phase:
- Discord: normalize() calls user_scoped() for guild channels → different users
  get different scope_ids → the equality assertion fails.
- Telegram: _make_scope_id() returns user_scoped(base, user_id) for groups →
  same failure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram.telegram_normalize import _make_scope_id

# ---------------------------------------------------------------------------
# Discord — two users in same guild channel
# ---------------------------------------------------------------------------


def _make_discord_adapter() -> DiscordAdapter:
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)
    return adapter


def _make_guild_message(
    guild_id: int, channel_id: int, user_id: int
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id, send=AsyncMock()),
        author=SimpleNamespace(
            id=user_id,
            name=f"User{user_id}",
            display_name=f"User{user_id}",
            bot=False,
        ),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=1000 + user_id,
        mentions=[],
        thread=None,
    )


def test_discord_two_users_same_guild_channel_same_scope_id() -> None:
    """Two users in the same guild channel must share the same scope_id.

    RED: currently discord_normalize calls user_scoped() for guild channels →
    user A: scope_id='channel:200:user:dc:user:1'
    user B: scope_id='channel:200:user:dc:user:2'
    → assertion fails.
    """
    adapter = _make_discord_adapter()

    msg_a = _make_guild_message(guild_id=100, channel_id=200, user_id=1)
    msg_b = _make_guild_message(guild_id=100, channel_id=200, user_id=2)

    normalized_a = adapter.normalize(msg_a)
    normalized_b = adapter.normalize(msg_b)

    sid_a = normalized_a.scope_id
    sid_b = normalized_b.scope_id

    # RED: fails because user_scoped() appends different user ids
    assert sid_a == sid_b, (
        f"Guild channel scope_ids must be identical for all users.\n"
        f"  user 1: {sid_a!r}\n"
        f"  user 2: {sid_b!r}"
    )


def test_discord_two_users_same_guild_channel_same_pool_id() -> None:
    """Two users in the same guild channel must map to the same pool_id.

    RED: scope_ids differ → pool_ids differ.
    """
    from lyra.core.hub.hub_protocol import RoutingKey
    from lyra.core.messaging.message import Platform

    adapter = _make_discord_adapter()

    msg_a = _make_guild_message(guild_id=100, channel_id=200, user_id=1)
    msg_b = _make_guild_message(guild_id=100, channel_id=200, user_id=2)

    norm_a = adapter.normalize(msg_a)
    norm_b = adapter.normalize(msg_b)

    key_a = RoutingKey(Platform.DISCORD, "main", norm_a.scope_id)
    key_b = RoutingKey(Platform.DISCORD, "main", norm_b.scope_id)

    # RED: pool_ids will differ because scope_ids differ
    assert key_a.to_pool_id() == key_b.to_pool_id(), (
        f"Both users must resolve to the same pool.\n"
        f"  pool A: {key_a.to_pool_id()!r}\n"
        f"  pool B: {key_b.to_pool_id()!r}"
    )


def test_discord_dm_scope_id_is_channel_only() -> None:
    """Discord DM (guild=None) → scope_id is just 'channel:<id>' (no user suffix).

    This should already pass; included as a regression guard.
    """
    adapter = _make_discord_adapter()

    dm_msg = SimpleNamespace(
        guild=None,
        channel=SimpleNamespace(id=777, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        thread=None,
    )

    normalized = adapter.normalize(dm_msg)

    assert normalized.scope_id == "channel:777", (
        f"DM scope_id should be 'channel:777', got {normalized.scope_id!r}"
    )


# ---------------------------------------------------------------------------
# Telegram — two users in same group
# ---------------------------------------------------------------------------


def test_telegram_two_users_same_group_same_scope_id() -> None:
    """Two users in the same Telegram group must share the same scope_id.

    RED: _make_scope_id returns user_scoped(base, user_id) for groups →
    user u1: scope_id='chat:300:user:tg:user:u1'
    user u2: scope_id='chat:300:user:tg:user:u2'
    → assertion fails.
    """
    sid_a = _make_scope_id(
        chat_id=300, topic_id=None, user_id="tg:user:u1", is_group=True
    )
    sid_b = _make_scope_id(
        chat_id=300, topic_id=None, user_id="tg:user:u2", is_group=True
    )

    # RED: fails because user_scoped() appends different user ids
    assert sid_a == sid_b, (
        f"Group scope_ids must be identical for all users.\n"
        f"  user u1: {sid_a!r}\n"
        f"  user u2: {sid_b!r}"
    )


def test_telegram_two_users_same_topic_same_scope_id() -> None:
    """Two users in the same topic must share the same scope_id.

    RED: topic scoping also calls user_scoped → different scope_ids per user.
    """
    sid_a = _make_scope_id(chat_id=300, topic_id=5, user_id="tg:user:u1", is_group=True)
    sid_b = _make_scope_id(chat_id=300, topic_id=5, user_id="tg:user:u2", is_group=True)

    # RED: fails because user_scoped() appends different user ids
    assert sid_a == sid_b, (
        f"Topic scope_ids must be identical for all users.\n"
        f"  user u1: {sid_a!r}\n"
        f"  user u2: {sid_b!r}"
    )


def test_telegram_private_scope_id_is_chat_only() -> None:
    """Telegram private chat → scope_id is 'chat:<id>' (no user suffix).

    This should already pass; included as regression guard.
    """
    sid = _make_scope_id(
        chat_id=100, topic_id=None, user_id="tg:user:42", is_group=False
    )

    assert sid == "chat:100", f"Private chat scope_id should be 'chat:100', got {sid!r}"


def test_telegram_different_groups_different_scope_id() -> None:
    """Users in different groups must NOT share a scope_id.

    This is a sanity check — different group chats must remain isolated.
    Expected to pass in both RED and GREEN.
    """
    sid_a = _make_scope_id(
        chat_id=300, topic_id=None, user_id="tg:user:u1", is_group=True
    )
    sid_b = _make_scope_id(
        chat_id=400, topic_id=None, user_id="tg:user:u1", is_group=True
    )

    # Even after fix, different chat_ids must produce different scope_ids
    assert sid_a != sid_b, (
        f"Different groups must have different scope_ids: {sid_a!r} vs {sid_b!r}"
    )
