"""Tests for /circuit command and agent config behavior (SC-15, issue #66).

Covers:
  Slice 4 -- /circuit command (TestCircuitCommandAdminCheck,
             TestCircuitCommandOpenState, TestCircuitCommandNoRegistry)
  Slice 5 -- Agent config: plugins + commands (TestPluginsConfig)

The TOML loading path (load_agent_config) was removed in #346.
Tests that exercised TOML loading have been rewritten to use AgentRow +
agent_row_to_config.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lyra.core.auth.trust import TrustLevel
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_parser import CommandParser
from lyra.core.commands.command_router import CommandConfig, CommandRouter
from lyra.core.messaging.message import InboundMessage, Response, TelegramMeta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_registry_with_open(open_service: str) -> CircuitRegistry:
    """Build a CircuitRegistry where one named circuit has been tripped open."""
    registry = CircuitRegistry()
    for name in ("claude-cli", "telegram", "discord", "hub"):
        cb = CircuitBreaker(name, failure_threshold=1, recovery_timeout=60)
        if name == open_service:
            cb.record_failure()
        registry.register(cb)
    return registry


def make_circuit_router(registry: CircuitRegistry | None = None) -> CommandRouter:
    """Build a CommandRouter with circuit_registry set."""
    plugins_dir = Path(tempfile.mkdtemp())
    loader = CommandLoader(plugins_dir)
    return CommandRouter(
        command_loader=loader,
        enabled_plugins=[],
        circuit_registry=registry,
    )


def make_circuit_msg(
    user_id: str = "tg:user:42", *, is_admin: bool = False
) -> InboundMessage:
    """Build a /circuit InboundMessage from a Telegram user."""
    _parser = CommandParser()
    return InboundMessage(
        id="msg-circuit-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id=user_id,
        user_name="Admin",
        is_mention=False,
        text="/circuit",
        text_raw="/circuit",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
        command=_parser.parse("/circuit"),
    )


# ---------------------------------------------------------------------------
# Slice 4 -- /circuit command (SC-15)
# ---------------------------------------------------------------------------


class TestCircuitCommandAdminCheck:
    """Non-admin sender is denied; admin sender receives the status table (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_denied_for_non_admin(self) -> None:
        """SC-15: Non-admin sender -> 'This command is admin-only.'"""
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("claude-cli"))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42")  # is_admin=False by default

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()

    @pytest.mark.asyncio
    async def test_circuit_command_returns_table_for_admin(self) -> None:
        """SC-15: Admin sender -> gets formatted status table with all circuits."""
        registry = CircuitRegistry()
        for name in ("claude-cli", "telegram", "discord", "hub"):
            registry.register(CircuitBreaker(name))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Circuit Status" in response.content
        assert "claude-cli" in response.content
        assert "telegram" in response.content
        assert "discord" in response.content
        assert "hub" in response.content

    @pytest.mark.asyncio
    async def test_circuit_command_denied_when_not_admin(self) -> None:
        """SC-15: msg.is_admin=False -> /circuit denied (fail-closed)."""
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("claude-cli"))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42")  # is_admin=False by default

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()


class TestCircuitCommandOpenState:
    """OPEN circuits surface a 'retry in Xs' hint in the status table (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_shows_retry_after_for_open_circuit(self) -> None:
        """SC-15: OPEN circuit shows 'retry in Xs' in the table."""
        registry = make_registry_with_open("claude-cli")
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "claude-cli" in response.content
        assert "retry in" in response.content.lower()


class TestCircuitCommandNoRegistry:
    """Missing registry -> informational 'not configured' message (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_no_registry(self) -> None:
        """No circuit_registry -> 'Circuit breaker not configured.'"""
        router = make_circuit_router(registry=None)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "not configured" in response.content.lower()


# ---------------------------------------------------------------------------
# Slice 5 -- Agent config: plugins + commands via AgentRow
# ---------------------------------------------------------------------------


class TestPluginsConfig:
    """agent_row_to_config() parses plugins_json and commands_json from AgentRow."""

    def test_commands_enabled_from_agent_row(self) -> None:
        from lyra.core.agent.agent_db_loader import agent_row_to_config
        from lyra.core.agent.agent_models import AgentRow

        row = AgentRow(
            name="test_agent",
            backend="claude-cli",
            model="claude-haiku-4-5-20251001",
            plugins_json=json.dumps(["echo", "weather"]),
        )

        agent = agent_row_to_config(row)

        assert agent.commands_enabled == ("echo", "weather")

    def test_commands_enabled_defaults_to_empty(self) -> None:
        from lyra.core.agent.agent_db_loader import agent_row_to_config
        from lyra.core.agent.agent_models import AgentRow

        row = AgentRow(
            name="test_agent",
            backend="claude-cli",
            model="claude-haiku-4-5-20251001",
        )

        agent = agent_row_to_config(row)

        # Absent plugins_json -> empty tuple (default-open)
        assert agent.commands_enabled == ()

    def test_command_config_parsed_from_commands_json(self) -> None:
        from lyra.core.agent.agent_db_loader import agent_row_to_config
        from lyra.core.agent.agent_models import AgentRow

        commands_data = {
            "/help": {
                "builtin": True,
                "description": "List available commands",
            }
        }
        row = AgentRow(
            name="test_agent",
            backend="claude-cli",
            model="claude-haiku-4-5-20251001",
            commands_json=json.dumps(commands_data),
        )

        agent = agent_row_to_config(row)

        assert hasattr(agent, "commands")
        assert "/help" in agent.commands
        help_cfg = agent.commands["/help"]
        assert isinstance(help_cfg, CommandConfig)
        assert help_cfg.builtin is True
        assert help_cfg.description == "List available commands"
