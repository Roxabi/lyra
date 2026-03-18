"""conftest.py for adapters tests.

Patches httpx.Cookies.extract_cookies to handle relative URLs gracefully.
httpx 0.28 requires absolute URLs for cookie extraction (urllib.request.Request
raises ValueError on relative URLs). Since ASGI tests don't need cookie jar
functionality, we skip extraction when the request URL is relative.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.core.message import InboundMessage
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Shared test helpers for adapter tests
# ---------------------------------------------------------------------------


def make_tg_msg(
    chat_id: int = 42, message_id: int = 10, topic_id: int | None = None
) -> InboundMessage:
    return InboundMessage(
        id=f"telegram:tg:user:1:0:{message_id}",
        platform="telegram",
        bot_id="main",
        scope_id=f"chat:{chat_id}",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": chat_id,
            "message_id": message_id,
            "topic_id": topic_id,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_dc_msg(channel_id: int = 99, message_id: int = 55) -> InboundMessage:
    return InboundMessage(
        id=f"discord:dc:user:1:0:{message_id}",
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:1",
        user_name="Bob",
        is_mention=False,
        text="hi",
        text_raw="hi",
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 1,
            "channel_id": channel_id,
            "message_id": message_id,
            "thread_id": None,
            "channel_type": "text",
        },
        trust_level=TrustLevel.TRUSTED,
    )


def make_tg_adapter() -> TelegramAdapter:
    hub = MagicMock()
    adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub)
    bot_mock = AsyncMock()
    bot_mock.send_voice = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def make_dc_adapter() -> DiscordAdapter:
    hub = MagicMock()
    return DiscordAdapter(hub=hub, bot_id="main")


def make_dc_inbound_msg(
    *,
    channel_id: int = 333,
    message_id: int = 555,
    is_mention: bool = False,
    msg_id: str = "msg-1",
) -> InboundMessage:
    """Build an InboundMessage matching the standard discord test fixture values."""
    return InboundMessage(
        id=msg_id,
        platform="discord",
        bot_id="main",
        scope_id=f"channel:{channel_id}",
        user_id="dc:user:42",
        user_name="Alice",
        is_mention=is_mention,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        trust_level=TrustLevel.TRUSTED,
        platform_meta={
            "guild_id": 111,
            "channel_id": channel_id,
            "message_id": message_id,
            "thread_id": None,
            "channel_type": "text",
        },
    )


def attach_typing_cm(mock_channel: MagicMock) -> None:
    """Attach a valid async context manager to mock_channel.typing().

    discord.py's Messageable.typing() returns an async context manager.
    AsyncMock's default auto-spec returns a coroutine instead, which
    causes ``async with messageable.typing()`` to raise TypeError.
    Call this helper on every mock channel used with adapter.send() or
    adapter.send_streaming().
    """
    mock_typing_cm = AsyncMock()
    mock_typing_cm.__aenter__ = AsyncMock(return_value=None)
    mock_typing_cm.__aexit__ = AsyncMock(return_value=False)
    mock_channel.typing = MagicMock(return_value=mock_typing_cm)


def mock_channel() -> MagicMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    return ch


_original_extract_cookies = httpx.Cookies.extract_cookies


def _safe_extract_cookies(self, response: httpx.Response) -> None:
    """Skip cookie extraction for responses to relative-URL requests."""
    try:
        _original_extract_cookies(self, response)
    except ValueError:
        # Relative URL in ASGI transport test — no cookies to extract.
        pass


@pytest.fixture(autouse=True)
def patch_httpx_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-use fixture: make httpx.Cookies.extract_cookies safe with relative URLs."""
    monkeypatch.setattr(httpx.Cookies, "extract_cookies", _safe_extract_cookies)
