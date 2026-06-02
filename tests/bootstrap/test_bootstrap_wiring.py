"""Tests for bootstrap wiring — hub.register_authenticator is called (Finding I)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from factory.bootstrap.wiring.bootstrap_wiring import TelegramWiringDeps
from factory.config import TelegramBotConfig
from factory.core.auth.authenticator import Authenticator, AuthenticatorDeps
from factory.core.auth.trust import (
    TrustLevel,  # noqa: F401 — used in Authenticator(default=)
)
from factory.core.hub.hub import Hub
from factory.core.messaging.message import Platform
from tests.conftest import _LOAD_BOT_TOKEN_PATH

# ---------------------------------------------------------------------------
# Finding I: wire_telegram_adapters registers the authenticator on the hub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wire_telegram_adapters_registers_authenticator() -> None:
    """wire_telegram_adapters() must call hub.register_authenticator() with the auth."""
    from factory.bootstrap.wiring.bootstrap_wiring import wire_telegram_adapters
    from factory.core.lifecycle.circuit_breaker import CircuitRegistry

    # Arrange — real Hub so register_authenticator actually records the call
    hub = Hub()

    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(
        AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.PUBLIC)
    )

    # bot_agent_map maps ("telegram", bot_id) → agent_name
    bot_agent_map: dict[tuple[str, str], str] = {("telegram", "main"): "lyra_default"}

    circuit_registry = CircuitRegistry()

    msg_manager = MagicMock()
    msg_manager.get.return_value = None

    # Patch TelegramAdapter so we don't make real HTTP calls.
    # resolve_identity() is an async method that calls the Telegram API — mock it.
    from unittest.mock import AsyncMock

    mock_adapter_instance = MagicMock()
    mock_adapter_instance.resolve_identity = AsyncMock()

    with (
        patch(
            "factory.bootstrap.wiring.bootstrap_wiring.TelegramAdapter",
            return_value=mock_adapter_instance,
        ),
        patch(
            _LOAD_BOT_TOKEN_PATH,
            return_value=("fake-token", "fake-secret"),
        ),
    ):
        # Act
        adapters, dispatchers = await wire_telegram_adapters(
            TelegramWiringDeps(
                hub=hub,
                tg_bot_auths=[(bot_cfg, auth)],
                bot_agent_map=bot_agent_map,
                circuit_registry=circuit_registry,
                msg_manager=msg_manager,
            )
        )

    # Assert — hub._authenticators should have the entry for (TELEGRAM, "main")
    assert (Platform.TELEGRAM, "main") in hub._authenticators
    registered_auth = hub._authenticators[(Platform.TELEGRAM, "main")]
    assert registered_auth is auth

    # Sanity: one adapter and one dispatcher were returned
    assert len(adapters) == 1
    assert len(dispatchers) == 1


@pytest.mark.asyncio
async def test_wire_telegram_no_nats_listener_in_dev_mode() -> None:
    """wire_telegram_adapters() does NOT create NatsOutboundListener (dev mode only)."""
    from factory.bootstrap.wiring.bootstrap_wiring import wire_telegram_adapters
    from factory.core.lifecycle.circuit_breaker import CircuitRegistry

    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(
        AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.PUBLIC)
    )

    from unittest.mock import AsyncMock

    mock_adapter_instance = MagicMock()
    mock_adapter_instance.resolve_identity = AsyncMock()
    mock_adapter_instance._outbound_listener = None

    with (
        patch(
            "factory.bootstrap.wiring.bootstrap_wiring.TelegramAdapter",
            return_value=mock_adapter_instance,
        ),
        patch(
            _LOAD_BOT_TOKEN_PATH,
            return_value=("fake-token", "fake-secret"),
        ),
    ):
        adapters, _ = await wire_telegram_adapters(
            TelegramWiringDeps(
                hub=hub,
                tg_bot_auths=[(bot_cfg, auth)],
                bot_agent_map={("telegram", "main"): "lyra_default"},
                circuit_registry=CircuitRegistry(),
                msg_manager=MagicMock(),
            )
        )

    assert len(adapters) == 1
    # In dev/embedded mode, no NATS listener is attached
    assert mock_adapter_instance._outbound_listener is None


@pytest.mark.asyncio
async def test_wire_telegram_adapters_skips_missing_agent_mapping() -> None:
    """wire_telegram_adapters() skips bots not in bot_agent_map without raising."""
    from factory.bootstrap.wiring.bootstrap_wiring import wire_telegram_adapters
    from factory.core.lifecycle.circuit_breaker import CircuitRegistry

    # Arrange
    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="orphan_bot")
    auth = Authenticator(
        AuthenticatorDeps(store=None, role_map={}, default=TrustLevel.PUBLIC)
    )

    circuit_registry = CircuitRegistry()
    msg_manager = MagicMock()

    # Act — bot_agent_map is empty so "orphan_bot" has no agent,
    # and the function returns before _load_bot_token is called.
    adapters, dispatchers = await wire_telegram_adapters(
        TelegramWiringDeps(
            hub=hub,
            tg_bot_auths=[(bot_cfg, auth)],
            bot_agent_map={},
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
        )
    )

    # Assert — nothing registered, nothing returned
    assert adapters == []
    assert dispatchers == []
    assert (Platform.TELEGRAM, "orphan_bot") not in hub._authenticators
