"""Tests for Discord audio attachment normalization (issue #140)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.core.message import InboundAudio


def _make_audio_attachment(content_type: str = "audio/ogg") -> SimpleNamespace:
    return SimpleNamespace(
        content_type=content_type,
        url="https://cdn.example/audio.ogg",
    )


def _make_discord_msg(
    attachments: list | None = None,
    channel_type: str = "text",
    thread_id: int | None = None,
) -> SimpleNamespace:
    if channel_type == "thread":
        channel: object = MagicMock(spec=discord.Thread)
        channel.id = thread_id or 555
    else:
        channel = SimpleNamespace(
            id=333,
            send=AsyncMock(),
            type=discord.ChannelType.text,
        )
    return SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=channel,
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="",
        created_at=datetime.now(timezone.utc),
        id=777,
        mentions=[],
        attachments=attachments or [],
    )


def _make_adapter() -> DiscordAdapter:
    hub = MagicMock()
    return DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())


# ---------------------------------------------------------------------------
# normalize_audio() field correctness
# ---------------------------------------------------------------------------


def test_normalize_audio_attachment_fields() -> None:
    """normalize_audio returns InboundAudio with correct fields for audio attachment."""
    adapter = _make_adapter()
    msg = _make_discord_msg(attachments=[_make_audio_attachment("audio/ogg")])
    result = adapter.normalize_audio(msg, b"bytes", "audio/ogg")
    assert isinstance(result, InboundAudio)
    assert result.id.startswith("discord:dc:user:42:")
    assert result.scope_id == "channel:333"
    assert result.mime_type == "audio/ogg"
    assert result.duration_ms is None
    assert result.file_id is None
    assert result.user_id == "dc:user:42"
    assert result.platform == "discord"
    assert result.bot_id == "main"
    assert result.audio_bytes == b"bytes"
    assert result.trust == "user"


def test_normalize_audio_channel_scope_id() -> None:
    """Regular channel → scope_id='channel:<id>'."""
    adapter = _make_adapter()
    msg = _make_discord_msg()
    result = adapter.normalize_audio(msg, b"x", "audio/ogg")
    assert result.scope_id == "channel:333"


def test_normalize_audio_thread_scope_id() -> None:
    """Thread channel → scope_id='thread:<id>'."""
    adapter = _make_adapter()
    msg = _make_discord_msg(channel_type="thread", thread_id=555)
    result = adapter.normalize_audio(msg, b"x", "audio/ogg")
    assert result.scope_id == "thread:555"


# ---------------------------------------------------------------------------
# on_message() audio attachment detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_calls_normalize_audio_for_audio_attachment() -> None:
    """on_message() calls normalize_audio() when an audio attachment is detected."""
    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        read=AsyncMock(return_value=b"ogg_bytes"),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False

    called_with: list = []
    original = adapter.normalize_audio

    def spy(m, ab, mt):
        called_with.append((ab, mt))
        return original(m, ab, mt)

    adapter.normalize_audio = spy  # type: ignore[method-assign]

    await adapter.on_message(msg)

    assert len(called_with) == 1
    assert called_with[0] == (b"ogg_bytes", "audio/ogg")


@pytest.mark.asyncio
async def test_on_message_does_not_call_normalize_audio_for_non_audio() -> None:
    """on_message() does not call normalize_audio() for non-audio attachments."""
    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    image_attachment = SimpleNamespace(
        content_type="image/png",
        url="https://cdn.example/image.png",
        read=AsyncMock(return_value=b"png"),
    )
    msg = _make_discord_msg(attachments=[image_attachment])
    msg.author.bot = False

    called = False
    original = adapter.normalize_audio

    def spy(m, ab, mt):
        nonlocal called
        called = True
        return original(m, ab, mt)

    adapter.normalize_audio = spy  # type: ignore[method-assign]

    await adapter.on_message(msg)

    assert not called


# ---------------------------------------------------------------------------
# on_message() returns after audio (B1 fix) — text path skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_returns_after_audio_skips_text_path() -> None:
    """on_message() must not enqueue a text hub_msg when audio is processed."""
    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=1024,
        read=AsyncMock(return_value=b"ogg_bytes"),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False

    await adapter.on_message(msg)

    hub.inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# on_message() size guard (S1 fix) — oversized audio dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_drops_oversized_audio_attachment() -> None:
    """on_message() silently drops audio attachments exceeding LYRA_MAX_AUDIO_BYTES."""
    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    adapter = DiscordAdapter(hub=hub, bot_id="main", intents=discord.Intents.none())
    adapter._max_audio_bytes = 100  # type: ignore[reportAttributeAccessIssue]

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=999,  # exceeds 100-byte limit
        read=AsyncMock(return_value=b"x" * 999),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False

    await adapter.on_message(msg)

    attachment_obj.read.assert_not_called()
    hub.inbound_bus.put.assert_not_called()
