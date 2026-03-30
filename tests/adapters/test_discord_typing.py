"""Tests for DiscordAdapter typing indicator lifecycle (T2 / T2b)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from lyra.adapters.discord import _ALLOW_ALL
from lyra.core.message import OutboundMessage
from lyra.core.render_events import TextRenderEvent

from .conftest import make_dc_inbound_msg

# ---------------------------------------------------------------------------
# T2 (typing indicator) — two-phase design: start on receipt, cancel on send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discord_typing_worker_uses_typing_context_manager() -> None:
    """_discord_typing_worker calls channel.typing() and exits cleanly on cancel."""
    import asyncio

    from lyra.adapters.discord import _discord_typing_worker

    mock_channel = AsyncMock()
    mock_channel.typing = AsyncMock()

    async def resolve(_channel_id: int) -> AsyncMock:
        return mock_channel

    task = asyncio.create_task(_discord_typing_worker(resolve, channel_id=123))
    await asyncio.sleep(0)  # yield to let the worker call typing()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    mock_channel.typing.assert_called_once()


@pytest.mark.asyncio
async def test_discord_typing_worker_handles_exception_gracefully() -> None:
    """_discord_typing_worker logs and exits cleanly when channel resolution fails."""
    from lyra.adapters.discord import _discord_typing_worker

    async def resolve(_channel_id: int) -> None:
        raise Exception("channel not found")

    # Should not raise
    await _discord_typing_worker(resolve, channel_id=123)


@pytest.mark.asyncio
async def test_start_typing_creates_background_task() -> None:
    """_start_typing() creates a task in _typing_tasks for the given channel."""
    import asyncio

    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    mock_channel = AsyncMock()
    mock_channel.typing = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    adapter._start_typing(333)
    await asyncio.sleep(0)  # let task start

    assert 333 in adapter._typing_tasks
    assert not adapter._typing_tasks[333].done()

    adapter._cancel_typing(333)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancel_typing_cancels_task() -> None:
    """_cancel_typing() cancels the background task and removes it from the dict."""
    import asyncio

    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    mock_channel = AsyncMock()
    mock_channel.typing = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    adapter._start_typing(333)
    await asyncio.sleep(0)

    adapter._cancel_typing(333)
    await asyncio.sleep(0)

    assert 333 not in adapter._typing_tasks


@pytest.mark.asyncio
async def test_send_cancels_typing_task_at_start() -> None:
    """send() calls _cancel_typing() before writing the response."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=AsyncMock(id=999))
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    cancelled_ids: list[int] = []
    original_cancel = adapter._cancel_typing

    def spy_cancel(send_to_id: int) -> None:
        cancelled_ids.append(send_to_id)
        original_cancel(send_to_id)

    object.__setattr__(adapter, "_cancel_typing", spy_cancel)

    await adapter.send(
        make_dc_inbound_msg(is_mention=False), OutboundMessage.from_text("hi")
    )  # noqa: E501

    assert 333 in cancelled_ids


@pytest.mark.asyncio
async def test_send_streaming_cancels_typing_task_at_start() -> None:
    """send_streaming() calls _cancel_typing() before writing the response."""
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )

    mock_placeholder = AsyncMock()
    mock_placeholder.id = 888
    mock_placeholder.edit = AsyncMock()

    mock_message = AsyncMock()
    mock_message.reply = AsyncMock(return_value=mock_placeholder)
    mock_channel = AsyncMock()
    mock_channel.get_partial_message = MagicMock(return_value=mock_message)
    mock_channel.send = AsyncMock(return_value=mock_placeholder)
    adapter.get_channel = MagicMock(return_value=mock_channel)

    cancelled_ids: list[int] = []
    original_cancel = adapter._cancel_typing

    def spy_cancel(send_to_id: int) -> None:
        cancelled_ids.append(send_to_id)
        original_cancel(send_to_id)

    object.__setattr__(adapter, "_cancel_typing", spy_cancel)

    async def _chunks() -> AsyncIterator[TextRenderEvent]:
        yield TextRenderEvent(text="hello", is_final=False)
        yield TextRenderEvent(text=" world", is_final=True)

    await adapter.send_streaming(make_dc_inbound_msg(msg_id="msg-2"), _chunks())

    assert 333 in cancelled_ids


# ---------------------------------------------------------------------------
# T2b — on_message does NOT cancel typing on successful queue (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_does_not_cancel_typing_when_message_queued() -> None:
    """on_message() must NOT cancel the typing task when the hub accepts the message.

    The typing task should remain alive until send() is called.  The old
    ``finally: _cancel_typing`` bug cancelled typing immediately after the
    synchronous hub.put(), so the indicator never showed during processing.
    """
    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock()  # succeeds — no QueueFull

    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    mock_channel = AsyncMock()
    mock_channel.typing = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    discord_msg = SimpleNamespace(
        guild=None,  # DM — bypasses group-chat filter added in 9f9072d
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=AsyncMock(),
        attachments=[],
    )

    await adapter.on_message(discord_msg)

    # Typing task must still be alive — send() (called later by hub) cancels it.
    assert 333 in adapter._typing_tasks
    assert not adapter._typing_tasks[333].done()

    # Cleanup
    import asyncio as _asyncio

    adapter._cancel_typing(333)
    await _asyncio.sleep(0)


@pytest.mark.asyncio
async def test_on_message_cancels_typing_when_message_dropped_queue_full() -> None:
    """on_message() must cancel typing immediately when the bus is full (QueueFull)."""
    import asyncio as _asyncio

    from lyra.adapters.discord import DiscordAdapter

    hub = MagicMock()
    hub.inbound_bus = MagicMock()
    hub.inbound_bus.put = MagicMock(side_effect=_asyncio.QueueFull())

    adapter = DiscordAdapter(
        hub=hub, bot_id="main", intents=discord.Intents.none(), auth=_ALLOW_ALL
    )
    adapter._bot_user = SimpleNamespace(id=999, bot=True)

    mock_channel = AsyncMock()
    mock_channel.typing = AsyncMock()
    adapter.get_channel = MagicMock(return_value=mock_channel)

    discord_msg = SimpleNamespace(
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, send=AsyncMock()),
        author=SimpleNamespace(id=42, name="Alice", display_name="Alice", bot=False),
        content="hello",
        created_at=datetime.now(timezone.utc),
        id=555,
        mentions=[],
        reply=AsyncMock(),
        attachments=[],
    )

    await adapter.on_message(discord_msg)

    # Typing task must be gone — no point keeping it when we already replied.
    assert 333 not in adapter._typing_tasks
