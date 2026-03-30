"""Tests for DiscordAdapter outbound message sending and rendering."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import _ALLOW_ALL
from lyra.core.message import (
    Button,
    OutboundMessage,
)

from .conftest import attach_typing_cm, make_dc_inbound_msg

# ---------------------------------------------------------------------------
# Helpers local to this module
# ---------------------------------------------------------------------------


def _make_discord_adapter():
    """Build a DiscordAdapter with a MagicMock hub."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    return DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )


# ---------------------------------------------------------------------------
# T5 — Own messages (bot author) are filtered — hub.bus.put NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_own_message_is_filtered() -> None:
    """When message.author == adapter._bot_user, inbound_bus.put is never called."""
    from datetime import datetime, timezone

    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = AsyncMock()

    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    bot_user = SimpleNamespace(id=999, bot=True)
    adapter._bot_user = bot_user

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=bot_user,  # same object — own message
        content="I just replied",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
    )

    await adapter.on_message(discord_msg)

    hub.inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — send() calls msg.reply() when is_mention=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_on_mention() -> None:
    """adapter.send() calls msg.reply(text) when is_mention=True."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock()
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    attach_typing_cm(mock_channel)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = make_dc_inbound_msg(is_mention=True)
    await adapter.send(hub_msg, OutboundMessage.from_text("hi"))

    mock_channel.get_partial_message.assert_called_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T7 — send() calls channel.send() when is_mention=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_on_no_mention() -> None:
    """send() still replies to the trigger message even when is_mention=False."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock()
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    attach_typing_cm(mock_channel)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    hub_msg = make_dc_inbound_msg(is_mention=False)
    await adapter.send(hub_msg, OutboundMessage.from_text("hi"))

    mock_channel.get_partial_message.assert_called_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")


# ---------------------------------------------------------------------------
# T14 — send() stores bot's reply message_id in response.metadata (channel.send)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_channel_send() -> None:
    """send() via msg.reply() stores sent message id in metadata."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    sent_msg = SimpleNamespace(id=888)
    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=sent_msg)
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    attach_typing_cm(mock_channel)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    outbound = OutboundMessage.from_text("hi")
    await adapter.send(make_dc_inbound_msg(is_mention=False), outbound)

    mock_channel.get_partial_message.assert_called_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")
    assert outbound.metadata["reply_message_id"] == 888


# ---------------------------------------------------------------------------
# T15 — send() stores bot's reply message_id in response.metadata (msg.reply)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_reply_message_id_msg_reply() -> None:
    """send() via msg.reply() stores sent message id in outbound.metadata."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    sent_msg = SimpleNamespace(id=7777)
    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=sent_msg)
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    attach_typing_cm(mock_channel)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    outbound = OutboundMessage.from_text("hi")
    await adapter.send(make_dc_inbound_msg(is_mention=True), outbound)

    mock_channel.get_partial_message.assert_called_once_with(555)
    mock_message.reply.assert_awaited_once_with("hi")
    assert outbound.metadata["reply_message_id"] == 7777


# ---------------------------------------------------------------------------
# T16 — send() does NOT set reply_message_id when send fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_no_reply_message_id_on_failure() -> None:
    """send() must NOT set reply_message_id in metadata when the send call throws."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(side_effect=Exception("network error"))
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    attach_typing_cm(mock_channel)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    outbound = OutboundMessage.from_text("hi")

    # Act — send() now raises on failure (CB recording handled by OutboundDispatcher)
    with pytest.raises(Exception, match="network error"):
        await adapter.send(make_dc_inbound_msg(is_mention=False), outbound)

    assert "reply_message_id" not in outbound.metadata


# ---------------------------------------------------------------------------
# Slice 4 RED tests — DiscordAdapter rendering of OutboundMessage (#138)
# ---------------------------------------------------------------------------


class TestDiscordOutboundMessage:
    """Slice 4 RED tests — DiscordAdapter rendering of OutboundMessage."""

    @pytest.mark.asyncio
    async def test_send_accepts_outbound_message(self) -> None:
        """adapter.send(msg, OutboundMessage.from_text("hello")) replies to trigger."""
        adapter = _make_discord_adapter()

        sent_mock = SimpleNamespace(id=88)
        mock_message = AsyncMock()
        mock_message.reply = AsyncMock(return_value=sent_mock)
        mock_channel = AsyncMock()
        mock_channel.get_partial_message = MagicMock(return_value=mock_message)
        attach_typing_cm(mock_channel)
        adapter.get_channel = MagicMock(return_value=mock_channel)

        outbound = OutboundMessage.from_text("hello")
        await adapter.send(make_dc_inbound_msg(), outbound)

        mock_message.reply.assert_awaited()

    def test_render_text_empty_returns_no_chunks(self) -> None:
        """render_text("") returns [] — no empty-string chunk to send to the API."""
        from lyra.adapters.discord_formatting import render_text

        chunks = render_text("")
        assert chunks == []

    def test_render_text_chunks_at_2000(self) -> None:
        """render_text("x" * 2500) returns 2 chunks, each <= 2000 characters."""
        from lyra.adapters.discord_formatting import render_text

        text = "x" * 2500
        chunks = render_text(text)
        assert len(chunks) == 2
        assert all(len(c) <= 2000 for c in chunks)

    def test_render_buttons_none_when_empty(self) -> None:
        """render_buttons([]) returns None."""
        from lyra.adapters.discord_formatting import render_buttons

        result = render_buttons([])
        assert result is None

    def test_render_buttons_returns_view(self) -> None:
        """render_buttons([Button("Yes","yes")]) returns a discord.ui.View."""
        from lyra.adapters.discord_formatting import render_buttons

        result = render_buttons([Button("Yes", "yes")])
        assert isinstance(result, discord.ui.View)

    @pytest.mark.asyncio
    async def test_buttons_only_on_last_chunk(self) -> None:
        """Sending OutboundMessage with long content + buttons: first chunk is
        a reply (no view), second (last) chunk uses channel.send with view."""
        adapter = _make_discord_adapter()

        reply_calls: list[dict] = []
        send_calls: list[dict] = []

        async def capture_reply(*args: object, **kwargs: object) -> SimpleNamespace:
            reply_calls.append(dict(kwargs))
            return SimpleNamespace(id=len(reply_calls))

        async def capture_send(*args: object, **kwargs: object) -> SimpleNamespace:
            send_calls.append(dict(kwargs))
            return SimpleNamespace(id=100 + len(send_calls))

        mock_message = AsyncMock()
        mock_message.reply = capture_reply
        mock_channel = AsyncMock()
        mock_channel.get_partial_message = MagicMock(return_value=mock_message)
        mock_channel.send = capture_send
        attach_typing_cm(mock_channel)
        adapter.get_channel = MagicMock(return_value=mock_channel)

        outbound = OutboundMessage(
            content=["x" * 2500],
            buttons=[Button("Yes", "yes")],
        )
        await adapter.send(make_dc_inbound_msg(is_mention=False), outbound)

        assert len(reply_calls) == 2, f"Expected 2 reply calls, got {len(reply_calls)}"
        assert "view" not in reply_calls[0], "First chunk should have no view"
        assert reply_calls[1].get("view") is not None, "Last chunk should have view"
        assert len(send_calls) == 0, f"Expected 0 send calls, got {len(send_calls)}"

    @pytest.mark.asyncio
    async def test_reply_message_id_stored_in_metadata(self) -> None:
        """send() stores the reply message id in outbound.metadata."""
        adapter = _make_discord_adapter()

        sent_mock = SimpleNamespace(id=7654)
        mock_message = AsyncMock()
        mock_message.reply = AsyncMock(return_value=sent_mock)
        mock_channel = AsyncMock()
        mock_channel.get_partial_message = MagicMock(return_value=mock_message)
        attach_typing_cm(mock_channel)
        adapter.get_channel = MagicMock(return_value=mock_channel)

        outbound = OutboundMessage.from_text("hi")
        await adapter.send(make_dc_inbound_msg(), outbound)

        assert outbound.metadata.get("reply_message_id") == 7654
