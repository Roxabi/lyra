"""Tests for bootstrap wiring — hub.register_authenticator is called (Finding I)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.config import TelegramBotConfig
from lyra.core.authenticator import Authenticator
from lyra.core.hub.hub import Hub
from lyra.core.message import Platform
from lyra.core.trust import TrustLevel  # noqa: F401 — used in Authenticator(default=)

# ---------------------------------------------------------------------------
# Finding I: wire_telegram_adapters registers the authenticator on the hub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wire_telegram_adapters_registers_authenticator() -> None:
    """wire_telegram_adapters() must call hub.register_authenticator() with the auth."""
    from lyra.bootstrap.bootstrap_wiring import wire_telegram_adapters
    from lyra.core.circuit_breaker import CircuitRegistry

    # Arrange — real Hub so register_authenticator actually records the call
    hub = Hub()

    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    # bot_agent_map maps ("telegram", bot_id) → agent_name
    bot_agent_map: dict[tuple[str, str], str] = {("telegram", "main"): "lyra_default"}

    # Mock CredentialStore: get_full returns (token, webhook_secret)
    cred_store = MagicMock()
    cred_store.get_full = AsyncMock(return_value=("fake-token", "fake-secret"))

    circuit_registry = CircuitRegistry()

    msg_manager = MagicMock()
    msg_manager.get.return_value = None

    # Patch TelegramAdapter so we don't make real HTTP calls.
    # resolve_identity() is an async method that calls the Telegram API — mock it.
    mock_adapter_instance = MagicMock()
    mock_adapter_instance.resolve_identity = AsyncMock()

    with patch(
        "lyra.bootstrap.bootstrap_wiring.TelegramAdapter",
        return_value=mock_adapter_instance,
    ):
        # Act
        adapters, dispatchers = await wire_telegram_adapters(
            hub=hub,
            tg_bot_auths=[(bot_cfg, auth)],
            bot_agent_map=bot_agent_map,
            cred_store=cred_store,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
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
    from lyra.bootstrap.bootstrap_wiring import wire_telegram_adapters
    from lyra.core.circuit_breaker import CircuitRegistry

    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="main")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    cred_store = MagicMock()
    cred_store.get_full = AsyncMock(return_value=("fake-token", "fake-secret"))

    mock_adapter_instance = MagicMock()
    mock_adapter_instance.resolve_identity = AsyncMock()
    mock_adapter_instance._outbound_listener = None

    with patch(
        "lyra.bootstrap.bootstrap_wiring.TelegramAdapter",
        return_value=mock_adapter_instance,
    ):
        adapters, _ = await wire_telegram_adapters(
            hub=hub,
            tg_bot_auths=[(bot_cfg, auth)],
            bot_agent_map={("telegram", "main"): "lyra_default"},
            cred_store=cred_store,
            circuit_registry=CircuitRegistry(),
            msg_manager=MagicMock(),
        )

    assert len(adapters) == 1
    # In dev/embedded mode, no NATS listener is attached
    assert mock_adapter_instance._outbound_listener is None


@pytest.mark.asyncio
async def test_wire_telegram_adapters_skips_missing_agent_mapping() -> None:
    """wire_telegram_adapters() skips bots not in bot_agent_map without raising."""
    from lyra.bootstrap.bootstrap_wiring import wire_telegram_adapters
    from lyra.core.circuit_breaker import CircuitRegistry

    # Arrange
    hub = Hub()
    bot_cfg = TelegramBotConfig(bot_id="orphan_bot")
    auth = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)

    cred_store = MagicMock()
    cred_store.get_full = AsyncMock(return_value=("token", None))

    circuit_registry = CircuitRegistry()
    msg_manager = MagicMock()

    # Act — bot_agent_map is empty so "orphan_bot" has no agent
    adapters, dispatchers = await wire_telegram_adapters(
        hub=hub,
        tg_bot_auths=[(bot_cfg, auth)],
        bot_agent_map={},
        cred_store=cred_store,
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
    )

    # Assert — nothing registered, nothing returned
    assert adapters == []
    assert dispatchers == []
    assert (Platform.TELEGRAM, "orphan_bot") not in hub._authenticators
