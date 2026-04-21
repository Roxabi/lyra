"""Integration test — Telegram adapter injects thread_session_id
into inbound message.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


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


def _make_private_tg_message(
    chat_id: int = 100,
    user_id: int = 42,
    text: str = "hello",
) -> SimpleNamespace:
    """Build a minimal aiogram-style private message SimpleNamespace."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, type="private"),
        from_user=SimpleNamespace(
            id=user_id,
            full_name="Alice",
            is_bot=False,
        ),
        text=text,
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        entities=None,
        message_id=1000 + user_id,
        photo=None,
        document=None,
        video=None,
        animation=None,
        sticker=None,
        voice=None,
        audio=None,
        video_note=None,
    )


def _make_telegram_adapter(fake_turn_store=None, mock_bus=None):
    """Build a TelegramAdapter with optional turn_store injection."""
    from lyra.adapters.telegram import TelegramAdapter

    if mock_bus is None:
        mock_bus = MagicMock()
        mock_bus.put_nowait = MagicMock()

    kwargs = dict(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=mock_bus,
    )
    if fake_turn_store is not None:
        kwargs["turn_store"] = fake_turn_store

    return TelegramAdapter(**kwargs), mock_bus  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_telegram_private_injects_thread_session_id() -> None:
    """TelegramAdapter with turn_store injects thread_session_id for private chats."""
    from lyra.adapters.telegram.telegram_inbound import (
        handle_message as telegram_handle_message,
    )

    mock_bus = MagicMock()
    mock_bus.put_nowait = MagicMock()
    # Also mock the async put path used by backpressure
    mock_bus.put = AsyncMock()

    fake_turn_store = _FakeTurnStore("prior-session-id")

    adapter, mock_bus = _make_telegram_adapter(
        fake_turn_store=fake_turn_store, mock_bus=mock_bus
    )

    # Wire a fake bot so _on_message can call bot.send_message if needed
    adapter.bot = AsyncMock()
    adapter.bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))
    adapter._bot_username = "lyra_bot"

    fake_msg = _make_private_tg_message(chat_id=100, user_id=42)

    # Patch _start_typing / _cancel_typing to no-ops (no real asyncio needed)
    adapter._start_typing = MagicMock()
    adapter._cancel_typing = MagicMock()

    await telegram_handle_message(adapter, fake_msg)

    # Verify the bus received a message
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


async def test_telegram_no_turn_store_no_injection() -> None:
    """Without turn_store, thread_session_id must not appear in platform_meta.

    This verifies backward compatibility: adapters without turn_store still work.
    Currently this trivially passes (no injection exists). It becomes a meaningful
    regression guard once injection is added.
    """
    from lyra.adapters.telegram.telegram_inbound import (
        handle_message as telegram_handle_message,
    )

    mock_bus = MagicMock()
    mock_bus.put_nowait = MagicMock()
    mock_bus.put = AsyncMock()

    # No turn_store — backward compatibility: should construct fine
    from lyra.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=mock_bus,
    )
    adapter.bot = AsyncMock()
    adapter.bot.get_me = AsyncMock(return_value=SimpleNamespace(username="lyra_bot"))
    adapter._bot_username = "lyra_bot"
    adapter._start_typing = MagicMock()
    adapter._cancel_typing = MagicMock()

    fake_msg = _make_private_tg_message(chat_id=100, user_id=42)

    await telegram_handle_message(adapter, fake_msg)

    if mock_bus.put_nowait.called:
        posted = mock_bus.put_nowait.call_args[0][1]
        assert "thread_session_id" not in posted.platform_meta, (
            "thread_session_id must not appear in platform_meta without turn_store"
        )


async def test_telegram_turn_store_attribute_stored() -> None:
    """TelegramAdapter must expose _turn_store after construction."""
    from lyra.adapters.telegram import TelegramAdapter

    mock_bus = MagicMock()
    fake_turn_store = _FakeTurnStore("session-xyz")

    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=mock_bus,
        turn_store=fake_turn_store,  # type: ignore[arg-type]
    )

    assert adapter._turn_store is fake_turn_store, (
        "TelegramAdapter must store turn_store as _turn_store attribute"
    )
