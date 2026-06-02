"""Tests for factory.bootstrap.wiring.nats_wiring — wire_nats_proxies."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from factory.bootstrap.wiring.nats_wiring import (
    NatsProxyWiringDeps,
    wire_nats_proxies,
)
from factory.config import TelegramBotConfig
from factory.core.auth.authenticator import Authenticator
from factory.core.hub import Hub
from factory.core.lifecycle.circuit_breaker import CircuitBreaker, CircuitRegistry
from factory.core.messaging.message import Platform

# ---------------------------------------------------------------------------
# test_wire_nats_proxies_skips_missing_bot
# ---------------------------------------------------------------------------


class TestWireNatsProxies:
    def test_wire_nats_proxies_skips_missing_bot_telegram(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """wire_nats_proxies skips bots absent from bot_agent_map (Telegram)."""
        # Arrange
        circuit_registry = CircuitRegistry()
        circuit_registry.register(CircuitBreaker(name="telegram"))

        hub = Hub(circuit_registry=circuit_registry)

        fake_nc = MagicMock()

        bot_cfg = TelegramBotConfig(bot_id="missing_bot", agent="some_agent")
        fake_auth: Authenticator = MagicMock(spec=Authenticator)

        bot_auths: list[tuple[TelegramBotConfig, Authenticator]] = [
            (bot_cfg, fake_auth)
        ]
        # bot_agent_map intentionally has no entry for ("telegram", "missing_bot")
        bot_agent_map: dict[tuple[str, str], str] = {}

        # Act
        with caplog.at_level(
            logging.WARNING, logger="factory.bootstrap.wiring.nats_wiring"
        ):
            proxies, dispatchers = wire_nats_proxies(
                NatsProxyWiringDeps(
                    hub=hub,
                    nc=fake_nc,
                    platform=Platform.TELEGRAM,
                    bot_auths=bot_auths,
                    bot_agent_map=bot_agent_map,
                    circuit_registry=circuit_registry,
                )
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
