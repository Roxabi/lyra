"""Tests for TelegramAdapter auth gate and HTTP auth/status endpoints.

Covers: TestTelegramAuth (S4 auth gate), T2 (missing secret → 401),
T9 (missing env var → SystemExit), SC-14 (GET /status returns all circuits).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.adapters.telegram import _ALLOW_ALL
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# File-local helpers
# ---------------------------------------------------------------------------


def _make_aiogram_msg(user_id: int = 42) -> object:
    """Build a minimal aiogram-like message SimpleNamespace."""
    return SimpleNamespace(
        chat=SimpleNamespace(id=123, type="private"),
        from_user=SimpleNamespace(id=user_id, full_name="Alice", is_bot=False),
        text="hello",
        date=datetime.now(timezone.utc),
        message_thread_id=None,
        message_id=1,
        entities=None,
    )


# ---------------------------------------------------------------------------
# T2 — Missing secret token → HTTP 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_secret_returns_401() -> None:
    """POST /webhooks/telegram/main without X-Telegram-Bot-Api-Secret-Token → 401."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter  # ImportError expected in RED

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main", token="test-token-secret", hub=hub, auth=_ALLOW_ALL
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter.app)
    ) as client:
        response = await client.post(
            "/webhooks/telegram/main",
            json={"update_id": 1},
        )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# T9 — Missing TELEGRAM_TOKEN env var → SystemExit
# ---------------------------------------------------------------------------


def test_missing_token_raises_on_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config() raises SystemExit with 'TELEGRAM_TOKEN' when env var is absent."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)

    from lyra.config import load_config  # ImportError expected in RED

    with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
        load_config()


# ---------------------------------------------------------------------------
# SC-14 — GET /status returns all 4 circuit states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_endpoint_returns_all_circuits() -> None:
    """SC-14: GET /status → JSON with all 4 circuit states."""
    import httpx

    from lyra.adapters.telegram import TelegramAdapter

    # Arrange — registry with all 4 circuits
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
        registry.register(
            CircuitBreaker(name, failure_threshold=3, recovery_timeout=60)
        )

    hub = MagicMock()
    adapter = TelegramAdapter(
        bot_id="main",
        token="test-token-secret",
        hub=hub,
        webhook_secret="secret",
        circuit_registry=registry,
        auth=_ALLOW_ALL,
    )

    # Act
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter.app)
    ) as client:
        response = await client.get(
            "/status",
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    services = data["services"]
    for name in ("anthropic", "telegram", "discord", "hub"):
        assert name in services, f"Missing circuit '{name}' in /status response"
        assert "state" in services[name]


# ---------------------------------------------------------------------------
# Slice S4: TelegramAdapter auth gate tests
# ---------------------------------------------------------------------------


class TestTelegramAuth:
    """Auth gate tests for TelegramAdapter._on_message and _on_voice_message."""

    @pytest.mark.asyncio
    async def test_blocked_user_skips_normalize(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """BLOCKED user: _on_message returns early without calling normalize()."""
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.BLOCKED
        auth.resolve.return_value = Identity(
            user_id="tg:user:42",
            trust_level=TrustLevel.BLOCKED,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)

        with caplog.at_level(logging.INFO, logger="lyra.adapters.telegram"):
            with patch.object(adapter, "normalize") as mock_norm:
                await adapter._on_message(_make_aiogram_msg())

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()
        assert any("auth_reject" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_allowed_user_has_trust_level(self) -> None:
        """TRUSTED user: message produced with correct trust_level."""
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.TRUSTED
        auth.resolve.return_value = Identity(
            user_id="tg:user:42",
            trust_level=TrustLevel.TRUSTED,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = AsyncMock()

        await adapter._on_message(_make_aiogram_msg())

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.TRUSTED
        assert msg.is_admin is False

    @pytest.mark.asyncio
    async def test_admin_user_has_is_admin_set(self) -> None:
        """Admin user: message produced with is_admin=True."""
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.resolve.return_value = Identity(
            user_id="tg:user:42",
            trust_level=TrustLevel.TRUSTED,
            is_admin=True,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = AsyncMock()

        await adapter._on_message(_make_aiogram_msg())

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.is_admin is True

    @pytest.mark.asyncio
    async def test_voice_blocked_skips_normalize(self) -> None:
        """BLOCKED user on voice: _on_voice_message returns early without sending."""
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.BLOCKED
        auth.resolve.return_value = Identity(
            user_id="tg:user:42",
            trust_level=TrustLevel.BLOCKED,
            is_admin=False,
        )

        hub = MagicMock()
        bot = AsyncMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = bot

        voice_msg = _make_aiogram_msg()
        with patch.object(adapter, "normalize_audio") as mock_norm_audio:
            await adapter._on_voice_message(voice_msg)

        # bot.send_message should NOT have been called (blocked before handling)
        bot.send_message.assert_not_called()
        mock_norm_audio.assert_not_called()

    @pytest.mark.asyncio
    async def test_public_user_message_forwarded(self) -> None:
        """PUBLIC user: message reaches bus with trust_level=TrustLevel.PUBLIC."""
        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.auth import AuthMiddleware
        from lyra.core.identity import Identity

        auth = MagicMock(spec=AuthMiddleware)
        auth.check.return_value = TrustLevel.PUBLIC
        auth.resolve.return_value = Identity(
            user_id="tg:user:42",
            trust_level=TrustLevel.PUBLIC,
            is_admin=False,
        )

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        hub.inbound_bus.put = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        adapter.bot = AsyncMock()

        await adapter._on_message(_make_aiogram_msg())

        hub.inbound_bus.put.assert_called_once()
        _platform, msg = hub.inbound_bus.put.call_args[0]
        assert msg.trust_level == TrustLevel.PUBLIC
        assert msg.is_admin is False

    @pytest.mark.asyncio
    async def test_integration_blocked_user_rejected_by_real_guard(self) -> None:
        """Integration: real Authenticator + real GuardChain rejects BLOCKED user."""
        from unittest.mock import patch

        from lyra.adapters.telegram import TelegramAdapter
        from lyra.core.authenticator import Authenticator
        from lyra.core.guard import BlockedGuard, GuardChain

        store = MagicMock()
        store.check.return_value = TrustLevel.BLOCKED
        auth = Authenticator(store=store, role_map={}, default=TrustLevel.BLOCKED)
        guard_chain = GuardChain([BlockedGuard()])

        hub = MagicMock()
        hub.inbound_bus = MagicMock()
        adapter = TelegramAdapter(bot_id="main", token="tok", hub=hub, auth=auth)
        # Inject real guard chain
        adapter._guard_chain = guard_chain

        with patch.object(adapter, "normalize") as mock_norm:
            await adapter._on_message(_make_aiogram_msg())

        mock_norm.assert_not_called()
        hub.inbound_bus.put.assert_not_called()
