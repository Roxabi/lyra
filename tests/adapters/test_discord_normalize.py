"""Tests for DiscordAdapter.normalize() — mention detection, display_name, token security."""  # noqa: E501

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import _ALLOW_ALL

# ---------------------------------------------------------------------------
# T2 — _normalize() builds correct DiscordContext
# ---------------------------------------------------------------------------


def test_normalize_builds_correct_discord_context() -> None:
    """normalize() on a discord message produces correct platform_meta."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED
    from lyra.core.message import InboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter.normalize(discord_msg)

    assert isinstance(msg, InboundMessage)
    assert msg.platform == "discord"
    assert msg.scope_id == "channel:333"
    assert msg.platform_meta["guild_id"] == 111
    assert msg.platform_meta["channel_id"] == 333
    assert msg.platform_meta["message_id"] == 555
    assert msg.platform_meta["channel_type"] == "text"


# ---------------------------------------------------------------------------
# T3 — is_mention True when bot is in message.mentions
# ---------------------------------------------------------------------------


def test_is_mention_true_when_bot_in_mentions() -> None:
    """bot_user present in message.mentions → is_mention is True."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="<@999> hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[bot_user],
    )

    msg = adapter.normalize(discord_msg)

    assert msg.is_mention is True


# ---------------------------------------------------------------------------
# T4 — is_mention False when bot is NOT in message.mentions
# ---------------------------------------------------------------------------


def test_is_mention_false_when_bot_not_in_mentions() -> None:
    """bot_user absent from message.mentions → is_mention is False."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter.normalize(discord_msg)

    assert msg.is_mention is False


# ---------------------------------------------------------------------------
# T10 — Cold-start: _bot_user=None → is_mention False, no crash
# ---------------------------------------------------------------------------


def test_normalize_bot_user_none_is_mention_false() -> None:
    """When _bot_user is None (pre-on_ready), is_mention must be False — never raise."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    # _bot_user stays None (default, before on_ready fires)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[SimpleNamespace(id=999)],  # would match if _bot_user were set
    )

    msg = adapter.normalize(discord_msg)

    assert msg.is_mention is False  # no crash, returns False


# ---------------------------------------------------------------------------
# T11 — Mention stripping: @mention prefix stripped from content
# ---------------------------------------------------------------------------


def test_mention_prefix_stripped_from_content() -> None:
    """@mention prefix (<@id>) is stripped from text before delivery."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="<@999> hello world",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[bot_user],
    )

    msg = adapter.normalize(discord_msg)

    assert msg.text == "hello world"
    assert msg.text_raw == "<@999> hello world"


def test_mention_prefix_stripped_nickname_variant() -> None:
    """@mention prefix with nickname format (<@!id>) is also stripped."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", bot=False),
        content="<@!999> hello world",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[bot_user],
    )

    msg = adapter.normalize(discord_msg)

    assert msg.text == "hello world"


# ---------------------------------------------------------------------------
# T12 — DM (guild=None) normalization: guild_id=0, no AttributeError
# ---------------------------------------------------------------------------


def test_normalize_dm_no_guild() -> None:
    """DM messages (guild=None) normalize with guild_id=None — no AttributeError."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.message import InboundMessage

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=None,  # DM — no guild
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter.normalize(discord_msg)

    assert isinstance(msg, InboundMessage)
    assert msg.platform_meta["guild_id"] is None
    assert msg.platform_meta["channel_id"] == 333
    assert msg.platform_meta["message_id"] == 555


# ---------------------------------------------------------------------------
# T13 — display_name: takes precedence over name when present
# ---------------------------------------------------------------------------


def test_normalize_uses_display_name_when_present() -> None:
    """When author has display_name, it takes precedence over name."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(
            id=42, name="alice_raw", display_name="Alice Display", bot=False
        ),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter.normalize(discord_msg)

    assert msg.user_name == "Alice Display"


def test_normalize_falls_back_to_name_when_display_name_none() -> None:
    """When display_name is None, falls back to name."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="alice_raw", display_name=None, bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    msg = adapter.normalize(discord_msg)

    assert msg.user_name == "alice_raw"


# ---------------------------------------------------------------------------
# A9 — Token-in-logs security test
# ---------------------------------------------------------------------------


def test_discord_token_not_in_logs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Discord bot token must never appear in log output at any log level."""
    import logging

    from lyra.adapters.discord import DiscordAdapter

    secret_token = "super-secret-discord-token-xyz"
    monkeypatch.setenv("DISCORD_TOKEN", secret_token)

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    with caplog.at_level(logging.DEBUG):
        discord_msg = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=333, send=AsyncMock()),
            author=SimpleNamespace(
                id=42, name="Alice", display_name="Alice", bot=False
            ),
            content="hello",
            created_at=datetime.now(timezone.utc),
            id=555,
            mentions=[],
        )
        adapter.normalize(discord_msg)

    for record in caplog.records:
        assert secret_token not in record.getMessage(), (
            f"Token found in log at {record.levelname}: {record.getMessage()!r}"
        )
