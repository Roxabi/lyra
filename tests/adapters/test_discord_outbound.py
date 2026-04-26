"""Tests for DiscordAdapter outbound message sending and rendering."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.core.messaging.message import (
    Button,
    DiscordMeta,
    OutboundMessage,
    TelegramMeta,
)

from .conftest import attach_typing_cm, make_dc_inbound_msg

# ---------------------------------------------------------------------------
# Helpers local to this module
# ---------------------------------------------------------------------------


def _make_discord_adapter():
    """Build a DiscordAdapter with a MagicMock hub."""
    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )


# ---------------------------------------------------------------------------
# T5 — Own messages (bot author) are filtered — hub.bus.put NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_own_message_is_filtered() -> None:
    """When message.author == adapter._bot_user, inbound_bus.put is never called."""
    from datetime import datetime, timezone

    from lyra.adapters.discord import DiscordAdapter  # ImportError expected in RED

    inbound_bus = MagicMock()
    inbound_bus.put = AsyncMock()

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=inbound_bus,
        intents=discord.Intents.none(),
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

    inbound_bus.put.assert_not_called()


# ---------------------------------------------------------------------------
# T6 — send() calls msg.reply() when is_mention=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_on_mention() -> None:
    """adapter.send() calls msg.reply(text) when is_mention=True."""
    from lyra.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
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

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
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

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
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

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
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

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
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
        from lyra.adapters.discord.discord_formatting import render_text

        chunks = render_text("")
        assert chunks == []

    def test_render_text_chunks_at_2000(self) -> None:
        """render_text("x" * 2500) returns 2 chunks, each <= 2000 characters."""
        from lyra.adapters.discord.discord_formatting import render_text

        text = "x" * 2500
        chunks = render_text(text)
        assert len(chunks) == 2
        assert all(len(c) <= 2000 for c in chunks)

    def test_render_buttons_none_when_empty(self) -> None:
        """render_buttons([]) returns None."""
        from lyra.adapters.discord.discord_formatting import render_buttons

        result = render_buttons([])
        assert result is None

    def test_render_buttons_returns_view(self) -> None:
        """render_buttons([Button("Yes","yes")]) returns a discord.ui.View."""
        from lyra.adapters.discord.discord_formatting import render_buttons

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


# ---------------------------------------------------------------------------
# V5 RED tests — DiscordAdapter MRO and fallback path (#468)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discord_mro_instantiation() -> None:
    """DiscordAdapter(discord.Client, OutboundAdapterBase) can be instantiated."""
    from unittest.mock import MagicMock

    import discord

    from lyra.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    assert adapter is not None


@pytest.mark.asyncio
async def test_discord_fallback_sets_reply_message_id() -> None:
    """When send_streaming fallback path is taken (placeholder fails),
    outbound.metadata['reply_message_id'] is set from the fallback message."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import InboundMessage, OutboundMessage

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    # Use a message with no message_id so should_reply=False (avoids reply() path)
    # Both placeholder and fallback go through channel.send()
    original_msg = InboundMessage(
        id="msg-fallback-test",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=DiscordMeta(
            guild_id=111,
            channel_id=333,
            message_id=0,  # no reply-to → should_reply=False
            thread_id=None,
            channel_type="text",
        ),
    )

    sent_mock = MagicMock()
    sent_mock.id = 88
    mock_channel = AsyncMock()
    # First call (send placeholder) raises; second call (fallback send) succeeds
    mock_channel.send = AsyncMock(
        side_effect=[Exception("placeholder failed"), sent_mock]
    )  # noqa: E501
    adapter._resolve_channel = AsyncMock(return_value=mock_channel)

    outbound = OutboundMessage.from_text("")

    async def _events():
        from lyra.core.messaging.render_events import TextRenderEvent

        yield TextRenderEvent(text="hello", is_final=True)

    await adapter.send_streaming(original_msg, _events(), outbound=outbound)
    assert outbound.metadata.get("reply_message_id") == 88


# ---------------------------------------------------------------------------
# #932 — Streaming callbacks (Slice 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_streaming_noop_on_non_discord_msg() -> None:
    """build_streaming_callbacks() with a non-discord msg returns noop callbacks
    whose send_placeholder raises ValueError."""
    from datetime import datetime, timezone

    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord.discord_outbound import build_streaming_callbacks
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import InboundMessage

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    non_discord_msg = InboundMessage(
        id="tg-msg-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=TelegramMeta(chat_id=1, message_id=1),
    )
    outbound = OutboundMessage.from_text("hi")

    callbacks = build_streaming_callbacks(adapter, non_discord_msg, outbound)

    with pytest.raises(ValueError, match="not a discord message"):
        await callbacks.send_placeholder()


@pytest.mark.asyncio
async def test_streaming_edit_placeholder_text() -> None:
    """edit_placeholder_text closure calls ph.edit(content=..., embed=None)."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    ph = AsyncMock()
    ph.edit = AsyncMock()

    outbound = OutboundMessage.from_text("")
    callbacks = build_streaming_callbacks(adapter, make_dc_inbound_msg(), outbound)
    await callbacks.edit_placeholder_text(ph, "hello world")

    ph.edit.assert_awaited_once_with(content="hello world", embed=None)


@pytest.mark.asyncio
async def test_streaming_edit_placeholder_tool() -> None:
    """edit_placeholder_tool closure calls ph.edit(content='', embed=<Embed>)."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord.discord_outbound import build_streaming_callbacks
    from lyra.core.messaging.render_events import ToolSummaryRenderEvent

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    ph = AsyncMock()
    ph.edit = AsyncMock()
    event = ToolSummaryRenderEvent(is_complete=True)

    outbound = OutboundMessage.from_text("")
    callbacks = build_streaming_callbacks(adapter, make_dc_inbound_msg(), outbound)
    await callbacks.edit_placeholder_tool(ph, event, "")

    ph.edit.assert_awaited_once()
    call_kwargs = ph.edit.call_args.kwargs
    assert call_kwargs["content"] == ""
    assert isinstance(call_kwargs["embed"], discord.Embed)


@pytest.mark.asyncio
async def test_streaming_send_message_multi_chunk() -> None:
    """send_message closure splits text > 2000 chars into chunks.

    Non-last chunks go through send_with_retry; last chunk goes via direct send.
    Returns the last sent message id.
    """
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    sent_ids = [SimpleNamespace(id=10), SimpleNamespace(id=20)]
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(side_effect=sent_ids)
    adapter._resolve_channel = AsyncMock(return_value=mock_channel)

    outbound = OutboundMessage.from_text("")
    callbacks = build_streaming_callbacks(adapter, make_dc_inbound_msg(), outbound)
    long_text = "x" * 2500
    result = await callbacks.send_message(long_text)

    assert result == 20
    assert mock_channel.send.await_count == 2


@pytest.mark.asyncio
async def test_streaming_send_message_failure() -> None:
    """send_message closure logs exception and returns None when final send raises."""
    from lyra.adapters.discord import DiscordAdapter
    from lyra.adapters.discord.discord_outbound import build_streaming_callbacks

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(side_effect=Exception("network error"))
    adapter._resolve_channel = AsyncMock(return_value=mock_channel)

    outbound = OutboundMessage.from_text("")
    callbacks = build_streaming_callbacks(adapter, make_dc_inbound_msg(), outbound)
    result = await callbacks.send_message("short text")

    assert result is None


# ---------------------------------------------------------------------------
# #932 — Tool embed + send edges (Slice 2)
# ---------------------------------------------------------------------------


def test_build_tool_embed_complete() -> None:
    """_build_tool_embed with is_complete=True produces a green Embed."""
    from lyra.adapters.discord.discord_outbound import _build_tool_embed
    from lyra.core.messaging.render_events import ToolSummaryRenderEvent

    event = ToolSummaryRenderEvent(is_complete=True)
    embed = _build_tool_embed(event)

    assert isinstance(embed, discord.Embed)
    assert embed.color == discord.Color.green()


def test_build_tool_embed_incomplete() -> None:
    """_build_tool_embed with is_complete=False produces a blue Embed."""
    from lyra.adapters.discord.discord_outbound import _build_tool_embed
    from lyra.core.messaging.render_events import ToolSummaryRenderEvent

    event = ToolSummaryRenderEvent(is_complete=False)
    embed = _build_tool_embed(event)

    assert isinstance(embed, discord.Embed)
    assert embed.color == discord.Color.blue()


@pytest.mark.asyncio
async def test_send_intermediate_starts_typing() -> None:
    """send() with outbound.intermediate=True calls adapter._start_typing."""
    from lyra.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    sent_msg = SimpleNamespace(id=100)
    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=sent_msg)
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    attach_typing_cm(mock_channel)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    started: list[int] = []
    original_start = adapter._start_typing

    def spy_start(scope_id: int) -> None:
        started.append(scope_id)
        original_start(scope_id)

    object.__setattr__(adapter, "_start_typing", spy_start)

    outbound = OutboundMessage(content=["hi"], intermediate=True)
    await adapter.send(make_dc_inbound_msg(), outbound)

    assert 333 in started

    # Cleanup typing tasks
    adapter._cancel_typing(333)


@pytest.mark.asyncio
async def test_send_thread_context_uses_channel_send() -> None:
    """send() with thread_id set uses messageable.send() not reply()."""
    from datetime import datetime, timezone

    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import InboundMessage

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    sent_msg = SimpleNamespace(id=200)
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(return_value=sent_msg)
    attach_typing_cm(mock_channel)
    adapter._resolve_channel = AsyncMock(return_value=mock_channel)

    thread_msg = InboundMessage(
        id="msg-thread-1",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=DiscordMeta(
            guild_id=111,
            channel_id=333,
            message_id=555,
            thread_id=777,
            channel_type="text",
        ),
    )

    outbound = OutboundMessage.from_text("reply in thread")
    await adapter.send(thread_msg, outbound)

    mock_channel.send.assert_awaited_once_with("reply in thread")


@pytest.mark.asyncio
async def test_send_thread_context_with_view() -> None:
    """send() with thread_id + buttons calls messageable.send(chunk, view=...)."""
    from datetime import datetime, timezone

    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import Button, InboundMessage

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    sent_msg = SimpleNamespace(id=201)
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock(return_value=sent_msg)
    attach_typing_cm(mock_channel)
    adapter._resolve_channel = AsyncMock(return_value=mock_channel)

    thread_msg = InboundMessage(
        id="msg-thread-2",
        platform="discord",
        bot_id="main",
        scope_id="channel:333",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=DiscordMeta(
            guild_id=111,
            channel_id=333,
            message_id=555,
            thread_id=777,
            channel_type="text",
        ),
    )

    outbound = OutboundMessage(content=["pick one"], buttons=[Button("Yes", "yes")])
    await adapter.send(thread_msg, outbound)

    mock_channel.send.assert_awaited_once()
    call_kwargs = mock_channel.send.call_args.kwargs
    assert call_kwargs.get("view") is not None


@pytest.mark.asyncio
async def test_send_invalid_inbound_returns_early() -> None:
    """send() with a non-discord InboundMessage returns without calling any API."""
    from datetime import datetime, timezone

    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.auth.trust import TrustLevel
    from lyra.core.messaging.message import InboundMessage

    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )

    mock_channel = AsyncMock()
    adapter._resolve_channel = AsyncMock(return_value=mock_channel)

    tg_msg = InboundMessage(
        id="tg-msg-invalid",
        platform="telegram",
        bot_id="main",
        scope_id="chat:1",
        user_id="tg:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta=TelegramMeta(chat_id=1, message_id=1),
    )

    await adapter.send(tg_msg, OutboundMessage.from_text("hi"))

    adapter._resolve_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_typing_worker_retry_resolve() -> None:
    """_discord_typing_worker retries resolve_channel on first failure.

    asyncio.sleep is patched at module level so backoff returns immediately.
    typing() raises CancelledError to break out of the inner loop cleanly.
    """
    import asyncio
    from unittest.mock import AsyncMock as _AsyncMock  # noqa: PLC0415

    import lyra.adapters.discord.discord_outbound as _outbound_mod
    from lyra.adapters.discord.discord_outbound import _discord_typing_worker

    call_count = 0
    mock_channel = _AsyncMock()

    async def typing_raises_cancelled():
        raise asyncio.CancelledError()

    mock_channel.typing = typing_raises_cancelled

    async def resolve(_channel_id: int):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("not cached yet")
        return mock_channel

    original_sleep = _outbound_mod.asyncio.sleep
    _outbound_mod.asyncio.sleep = _AsyncMock()
    try:
        # The worker will: fail resolve once, sleep (mocked), succeed on second attempt,
        # then enter typing loop where CancelledError from typing() breaks out cleanly.
        await _discord_typing_worker(resolve, channel_id=123)
    finally:
        _outbound_mod.asyncio.sleep = original_sleep

    assert call_count == 2


@pytest.mark.asyncio
async def test_typing_worker_bailout_after_3_errors() -> None:
    """_discord_typing_worker returns after 3 consecutive channel.typing() failures."""
    from unittest.mock import AsyncMock as _AsyncMock

    import lyra.adapters.discord.discord_outbound as _outbound_mod
    from lyra.adapters.discord.discord_outbound import _discord_typing_worker

    mock_channel = _AsyncMock()
    mock_channel.typing = _AsyncMock(side_effect=Exception("typing failed"))

    async def resolve(_channel_id: int):
        return mock_channel

    original_sleep = _outbound_mod.asyncio.sleep
    _outbound_mod.asyncio.sleep = _AsyncMock()
    try:
        await _discord_typing_worker(resolve, channel_id=456)
    finally:
        _outbound_mod.asyncio.sleep = original_sleep

    assert mock_channel.typing.call_count >= 3
