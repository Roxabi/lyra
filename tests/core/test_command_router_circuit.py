"""Tests for /circuit command and TOML plugins config parsing (SC-15, issue #66).

Covers:
  Slice 4 — /circuit command (TestCircuitCommandAdminCheck,
             TestCircuitCommandOpenState, TestCircuitCommandNoRegistry)
  Slice 5 — TOML plugins config parsing (TestPluginsConfigFromToml)
"""

from __future__ import annotations

import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lyra.core.agent import load_agent_config
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.command_loader import CommandLoader
from lyra.core.command_parser import CommandParser
from lyra.core.command_router import CommandConfig, CommandRouter
from lyra.core.message import InboundMessage, Response
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_minimal_toml(extra: str = "") -> str:
    """Return a minimal valid agent TOML string."""
    return textwrap.dedent(
        f"""
        [agent]
        name = "test_agent"
        memory_namespace = "test"
        permissions = []

        [model]
        backend = "claude-cli"
        model = "claude-haiku-4-5-20251001"
        max_turns = 10
        tools = []

        [prompt]
        system = "You are a test agent."

        {extra}
        """
    ).strip()


def make_registry_with_open(open_service: str) -> CircuitRegistry:
    """Build a CircuitRegistry where one named circuit has been tripped open."""
    registry = CircuitRegistry()
    for name in ("anthropic", "telegram", "discord", "hub"):
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
        platform_meta={
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
        command=_parser.parse("/circuit"),  # type: ignore[call-arg]
    )


# ---------------------------------------------------------------------------
# Slice 4 — /circuit command (SC-15)
# ---------------------------------------------------------------------------


class TestCircuitCommandAdminCheck:
    """Non-admin sender is denied; admin sender receives the status table (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_denied_for_non_admin(self) -> None:
        """SC-15: Non-admin sender → 'This command is admin-only.'"""
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("anthropic"))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42")  # is_admin=False by default

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "admin-only" in response.content.lower()

    @pytest.mark.asyncio
    async def test_circuit_command_returns_table_for_admin(self) -> None:
        """SC-15: Admin sender → gets formatted status table with all circuits."""
        registry = CircuitRegistry()
        for name in ("anthropic", "telegram", "discord", "hub"):
            registry.register(CircuitBreaker(name))
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "Circuit Status" in response.content
        assert "anthropic" in response.content
        assert "telegram" in response.content
        assert "discord" in response.content
        assert "hub" in response.content

    @pytest.mark.asyncio
    async def test_circuit_command_denied_when_not_admin(self) -> None:
        """SC-15: msg.is_admin=False → /circuit denied (fail-closed)."""
        registry = CircuitRegistry()
        registry.register(CircuitBreaker("anthropic"))
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
        registry = make_registry_with_open("anthropic")
        router = make_circuit_router(registry=registry)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "anthropic" in response.content
        assert "retry in" in response.content.lower()


class TestCircuitCommandNoRegistry:
    """Missing registry → informational 'not configured' message (SC-15)."""

    @pytest.mark.asyncio
    async def test_circuit_command_no_registry(self) -> None:
        """No circuit_registry → 'Circuit breaker not configured.'"""
        router = make_circuit_router(registry=None)
        msg = make_circuit_msg(user_id="tg:user:42", is_admin=True)

        response = await router.dispatch(msg)

        assert isinstance(response, Response)
        assert "not configured" in response.content.lower()


# ---------------------------------------------------------------------------
# Slice 5 — TOML plugins config parsing
# ---------------------------------------------------------------------------


class TestPluginsConfigFromToml:
    """load_agent_config() parses the [plugins] TOML section."""

    def test_commands_enabled_from_toml(self, tmp_path: Path) -> None:
        toml_content = make_minimal_toml(
            extra=textwrap.dedent(
                """
                [plugins]
                enabled = ["echo", "weather"]
                """
            )
        )
        (tmp_path / "test_agent.toml").write_text(toml_content)

        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        assert agent.commands_enabled == ("echo", "weather")

    def test_commands_enabled_defaults_to_empty(self, tmp_path: Path) -> None:
        toml_content = make_minimal_toml()
        (tmp_path / "test_agent.toml").write_text(toml_content)

        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        # Absent [plugins] section → empty tuple (default-open)
        assert agent.commands_enabled == ()

    def test_command_config_still_parsed_for_builtins(self, tmp_path: Path) -> None:
        toml_content = make_minimal_toml(
            extra=textwrap.dedent(
                """
                [commands."/help"]
                builtin = true
                description = "List available commands"
                """
            )
        )
        (tmp_path / "test_agent.toml").write_text(toml_content)

        agent = load_agent_config("test_agent", agents_dir=tmp_path)

        assert hasattr(agent, "commands")
        assert "/help" in agent.commands
        help_cfg = agent.commands["/help"]
        assert isinstance(help_cfg, CommandConfig)
        assert help_cfg.builtin is True
        assert help_cfg.description == "List available commands"
