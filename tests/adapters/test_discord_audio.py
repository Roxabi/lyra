"""Tests for Discord audio attachment normalization (issue #140)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.core.audio_payload import AudioPayload
from lyra.core.messaging.message import InboundMessage


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
        reply=AsyncMock(),
    )


def _make_adapter() -> DiscordAdapter:
    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )


# ---------------------------------------------------------------------------
# normalize_audio() field correctness
# ---------------------------------------------------------------------------


def test_normalize_audio_attachment_fields() -> None:
    """normalize_audio returns InboundMessage(modality='voice') with correct fields."""
    from lyra.core.auth.trust import TrustLevel

    adapter = _make_adapter()
    msg = _make_discord_msg(attachments=[_make_audio_attachment("audio/ogg")])
    result = adapter.normalize_audio(
        msg, b"bytes", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.modality == "voice"
    assert result.id.startswith("discord:dc:user:42:")
    assert result.scope_id == "channel:333:user:dc:user:42"
    assert result.user_id == "dc:user:42"
    assert result.platform == "discord"
    assert result.bot_id == "main"
    assert result.trust_level == TrustLevel.TRUSTED
    # Audio payload checks
    assert isinstance(result.audio, AudioPayload)
    assert result.audio.mime_type == "audio/ogg"
    assert result.audio.audio_bytes == b"bytes"
    assert result.audio.duration_ms is None
    assert result.audio.file_id is None
    assert result.audio.waveform_b64 is None


def test_normalize_audio_channel_scope_id() -> None:
    """Regular channel → scope_id='channel:<id>:user:<user_id>' (user-scoped)."""
    from lyra.core.auth.trust import TrustLevel

    adapter = _make_adapter()
    msg = _make_discord_msg()
    result = adapter.normalize_audio(
        msg, b"x", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.scope_id == "channel:333:user:dc:user:42"


def test_normalize_audio_thread_scope_id() -> None:
    """Thread channel → scope_id='thread:<id>'."""
    from lyra.core.auth.trust import TrustLevel

    adapter = _make_adapter()
    msg = _make_discord_msg(channel_type="thread", thread_id=555)
    result = adapter.normalize_audio(
        msg, b"x", "audio/ogg", trust_level=TrustLevel.TRUSTED
    )
    assert isinstance(result, InboundMessage)
    assert result.scope_id == "thread:555"


# ---------------------------------------------------------------------------
# on_message() audio attachment → download + enqueue on audio bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_enqueues_audio_on_inbound_bus() -> None:
    """on_message() downloads audio and enqueues InboundMessage(modality='voice') on inbound_bus."""  # noqa: E501
    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
    )

    # Use a valid OGG magic header so the magic-byte check passes.
    _OGG_MAGIC = b"OggS" + b"\x00" * 20
    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=1024,
        read=AsyncMock(return_value=_OGG_MAGIC),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False
    # Simulate a DM so the audio gate (not-DM, not-mention, not-owned-thread)
    # does not skip enqueue.
    msg.guild = None

    await adapter.on_message(msg)

    # Audio bytes downloaded
    attachment_obj.read.assert_called_once()
    # Enqueued on unified inbound_bus (not a separate audio bus)
    inbound_bus.put.assert_called_once()
    # put(platform, msg) — msg is the second positional arg
    enqueued = inbound_bus.put.call_args[0][1]
    assert isinstance(enqueued, InboundMessage)
    assert enqueued.modality == "voice"
    assert isinstance(enqueued.audio, AudioPayload)
    assert enqueued.audio.mime_type == "audio/ogg"
    # No unsupported reply sent
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_audio_download_failure_returns_cleanly() -> None:
    """on_message() handles download failure gracefully — no enqueue, no crash."""
    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
    )

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=1000,
        read=AsyncMock(side_effect=RuntimeError("network error")),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False

    await adapter.on_message(msg)

    inbound_bus.put.assert_not_called()
    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_on_message_audio_invalid_magic_bytes_sends_reply() -> None:
    """on_message() rejects audio whose magic bytes don't match any known format."""
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=1024,
        read=AsyncMock(return_value=b"INVALID_NOT_AUDIO"),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False
    msg.guild = None  # DM so auth gate passes

    await adapter.on_message(msg)

    # Should NOT enqueue — invalid format rejected before push
    # The adapter sends a reply about the invalid format
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_on_message_audio_too_large_sends_reply() -> None:
    """on_message() rejects oversized audio with user-facing reply."""
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=999_999_999,  # way over limit
        read=AsyncMock(return_value=b"ogg_bytes"),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False

    await adapter.on_message(msg)

    # Should NOT download
    attachment_obj.read.assert_not_called()
    # Should reply with too-large message
    msg.reply.assert_called_once()


@pytest.mark.asyncio
async def test_on_message_does_not_call_normalize_audio_for_non_audio() -> None:
    """on_message() does not call normalize_audio() for non-audio attachments."""
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    image_attachment = SimpleNamespace(
        content_type="image/png",
        url="https://cdn.example/image.png",
        read=AsyncMock(return_value=b"png"),
    )
    msg = _make_discord_msg(attachments=[image_attachment])
    msg.author.bot = False

    called = False
    original = adapter.normalize_audio

    def spy(m, ab, mt, *, trust_level):
        nonlocal called
        called = True
        return original(m, ab, mt, trust_level=trust_level)

    object.__setattr__(adapter, "normalize_audio", spy)

    await adapter.on_message(msg)

    assert not called


# ---------------------------------------------------------------------------
# on_message() returns after audio — text path skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_returns_after_audio_skips_text_path() -> None:
    """on_message() must not enqueue a text hub_msg when audio is processed."""
    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
    )

    attachment_obj = SimpleNamespace(
        content_type="audio/ogg",
        url="https://cdn.example/audio.ogg",
        size=1024,
        read=AsyncMock(return_value=b"ogg_bytes"),
    )
    msg = _make_discord_msg(attachments=[attachment_obj])
    msg.author.bot = False

    await adapter.on_message(msg)

    inbound_bus.put.assert_not_called()
