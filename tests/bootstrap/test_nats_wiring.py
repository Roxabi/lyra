"""Tests for lyra.bootstrap.nats_wiring — wire_nats_telegram_proxies."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from lyra.bootstrap.nats_wiring import wire_nats_telegram_proxies
from lyra.config import TelegramBotConfig
from lyra.core.authenticator import Authenticator
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub

# ---------------------------------------------------------------------------
# test_wire_nats_telegram_proxies_skips_missing_bot
# ---------------------------------------------------------------------------


class TestWireNatsTelegramProxies:
    def test_wire_nats_telegram_proxies_skips_missing_bot(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """wire_nats_telegram_proxies skips bots absent from bot_agent_map."""
        # Arrange
        circuit_registry = CircuitRegistry()
        circuit_registry.register(CircuitBreaker(name="telegram"))

        hub = Hub(circuit_registry=circuit_registry)

        fake_nc = MagicMock()

        bot_cfg = TelegramBotConfig(bot_id="missing_bot", agent="some_agent")
        fake_auth: Authenticator = MagicMock(spec=Authenticator)

        tg_bot_auths: list[tuple[TelegramBotConfig, Authenticator]] = [
            (bot_cfg, fake_auth)
        ]
        # bot_agent_map intentionally has no entry for ("telegram", "missing_bot")
        bot_agent_map: dict[tuple[str, str], str] = {}

        # Act
        with caplog.at_level(logging.WARNING, logger="lyra.bootstrap.nats_wiring"):
            proxies, dispatchers = wire_nats_telegram_proxies(
                hub=hub,
                nc=fake_nc,
                tg_bot_auths=tg_bot_auths,
                bot_agent_map=bot_agent_map,
                circuit_registry=circuit_registry,
            )

        # Assert — no proxy created and warning was logged
        assert proxies == [], "No NatsChannelProxy should be created for missing bot"
        assert dispatchers == [], "No OutboundDispatcher should be created"

        warning_messages = [
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any(
            "missing_bot" in msg or "not in bot_agent_map" in msg
            for msg in warning_messages
        ), f"Expected warning about missing_bot in: {warning_messages}"
