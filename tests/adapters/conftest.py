"""conftest.py for adapters tests.

Patches httpx.Cookies.extract_cookies to handle relative URLs gracefully.
httpx 0.28 requires absolute URLs for cookie extraction (urllib.request.Request
raises ValueError on relative URLs). Since ASGI tests don't need cookie jar
functionality, we skip extraction when the request URL is relative.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.core.auth.trust import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.messaging.message import InboundMessage, TelegramMeta

# Backward-compatible re-exports from adapter factories
from tests.factories.adapters import (  # noqa: F401
    attach_typing_cm,
    discord_adapter,
    make_dc_adapter,
    make_dc_attach_msg,
    make_dc_inbound_msg,
    make_dc_msg,
    make_tg_adapter,
    make_tg_attach_msg,
    make_tg_msg,
    mock_channel,
    telegram_adapter,
)

__all__ = [
    "attach_typing_cm",
    "discord_adapter",
    "make_dc_adapter",
    "make_dc_attach_msg",
    "make_dc_inbound_msg",
    "make_dc_msg",
    "make_tg_adapter",
    "make_tg_attach_msg",
    "make_tg_msg",
    "mock_channel",
    "telegram_adapter",
]

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
        platform_meta=TelegramMeta(chat_id=123, message_id=1),
        trust_level=TrustLevel.TRUSTED,
    )


def _make_open_registry(service: str) -> CircuitRegistry:
    """Build a CircuitRegistry with the named circuit tripped OPEN."""
    registry = CircuitRegistry()
    for name in ("claude-cli", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == service:
            cb.record_failure()  # trips to OPEN
        registry.register(cb)
    return registry


# ---------------------------------------------------------------------------
# Extended adapter builders for render_attachment tests
# ---------------------------------------------------------------------------


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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_inbound_bus():
    bus = MagicMock()
    bus.put = AsyncMock()
    return bus


@pytest.fixture(autouse=True)
def patch_blobstore_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-use fixture: mock HttpBlobStore so adapter tests don't hit the network."""
    from unittest.mock import MagicMock

    from roxabi_contracts import BlobRef
    from tests.helpers.messages import _TEST_BLOB_REGISTRY

    async def _mock_get(store_key: str) -> bytes:
        return _TEST_BLOB_REGISTRY.get(store_key, b"mock-audio-bytes")

    def _mock_put(data: bytes, *, mime: str, **kwargs: Any) -> BlobRef:
        ref = BlobRef(
            store_key="test-blob",
            content_hash="deadbeef",
            mime=mime,
            size=len(data),
            source="test",
        )
        _TEST_BLOB_REGISTRY[ref.store_key] = data
        return ref

    mock_store = MagicMock()
    mock_store.get = AsyncMock(side_effect=_mock_get)
    mock_store.put = AsyncMock(side_effect=_mock_put)
    for target in (
        "lyra.adapters.shared._blobstore_client.get_blobstore_client",
        "lyra.adapters.telegram.telegram_audio.get_blobstore_client",
        "lyra.adapters.discord.discord_audio_outbound.get_blobstore_client",
        "lyra.adapters.shared._shared_audio.get_blobstore_client",
    ):
        monkeypatch.setattr(target, lambda: mock_store)


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
