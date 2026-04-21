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
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
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
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=MagicMock(),
    )
    bot_mock = AsyncMock()
    bot_mock.send_voice = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def make_dc_adapter() -> DiscordAdapter:
    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
    )


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


# ---------------------------------------------------------------------------
# Extended message builders for render_attachment tests
# (support omit_* and thread_id / topic_id parameters)
# ---------------------------------------------------------------------------


def make_tg_attach_msg(
    chat_id: int = 42,
    message_id: int = 10,
    topic_id: int | None = None,
    *,
    omit_chat_id: bool = False,
) -> InboundMessage:
    """InboundMessage for Telegram render_attachment tests."""
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
        trust_level=TrustLevel.TRUSTED,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            **({"chat_id": chat_id} if not omit_chat_id else {}),
            "message_id": message_id,
            "topic_id": topic_id,
            "is_group": False,
        },
    )


def make_dc_attach_msg(
    channel_id: int = 99,
    message_id: int = 55,
    thread_id: int | None = None,
    *,
    omit_channel_id: bool = False,
) -> InboundMessage:
    """InboundMessage for Discord render_attachment tests."""
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
        trust_level=TrustLevel.TRUSTED,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "guild_id": 1,
            **({"channel_id": channel_id} if not omit_channel_id else {}),
            "message_id": message_id,
            "thread_id": thread_id,
            "channel_type": "text",
        },
    )


def make_tg_attach_adapter() -> TelegramAdapter:
    """TelegramAdapter with send_photo/send_video/send_document mocked."""
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=MagicMock(),
    )
    bot_mock = AsyncMock()
    bot_mock.send_photo = AsyncMock()
    bot_mock.send_video = AsyncMock()
    bot_mock.send_document = AsyncMock()
    adapter.bot = bot_mock
    return adapter


def make_dc_attach_adapter() -> DiscordAdapter:
    """DiscordAdapter for render_attachment tests."""
    return DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Outbound-send test helpers (used by test_telegram_outbound_send/render)
# ---------------------------------------------------------------------------


def _make_telegram_adapter() -> TelegramAdapter:
    """Build a TelegramAdapter with mock buses (no bot attached)."""
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        inbound_bus=MagicMock(),
        
    )
    return adapter


def _make_telegram_message() -> InboundMessage:
    """Build a minimal InboundMessage for adapter.send() calls."""
    return InboundMessage(
        id="msg-tg-138",
        platform="telegram",
        bot_id="main",
        scope_id="chat:123",
        user_id="tg:user:42",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta={"chat_id": 123, "message_id": 1},
        trust_level=TrustLevel.TRUSTED,
    )


def _make_open_registry(service: str) -> CircuitRegistry:
    """Build a CircuitRegistry with the named circuit tripped OPEN."""
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == service:
            cb.record_failure()  # trips to OPEN
        registry.register(cb)
    return registry


@pytest.fixture
def mock_inbound_bus():
    bus = MagicMock()
    bus.put = AsyncMock()
    return bus


@pytest.fixture
def telegram_adapter(mock_inbound_bus):
    adapter = TelegramAdapter(
        bot_id="main",
        token="tok",
        inbound_bus=mock_inbound_bus,
    )
    bot_mock = AsyncMock()
    bot_mock.send_voice = AsyncMock()
    adapter.bot = bot_mock
    return adapter


@pytest.fixture
def discord_adapter(mock_inbound_bus):
    return DiscordAdapter(
        bot_id="main",
        inbound_bus=mock_inbound_bus,
    )


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
