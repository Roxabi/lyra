"""Tests for lyra.bootstrap.factory.hub_builder — build_cli_pool and register_agents."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lyra.bootstrap.factory.hub_builder import build_cli_pool, register_agents
from lyra.core.agent import Agent
from lyra.core.agent.agent_config import ModelConfig
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.hub import Hub

# ---------------------------------------------------------------------------
# test_build_cli_pool_returns_none_without_claude_cli
# ---------------------------------------------------------------------------


class TestBuildCliPool:
    async def test_build_cli_pool_returns_none_without_claude_cli(self) -> None:
        """build_cli_pool returns None when no agent uses the claude-cli backend."""
        # Arrange — two agents, neither using claude-cli
        agent_configs: dict[str, Agent] = {
            "agent_a": Agent(
                name="agent_a",
                system_prompt="prompt",
                memory_namespace="test",
                llm_config=ModelConfig(backend="nats"),
            ),
            "agent_b": Agent(
                name="agent_b",
                system_prompt="prompt",
                memory_namespace="test",
                llm_config=ModelConfig(backend="ollama"),
            ),
        }
        raw_config: dict = {}

        # Act
        result = await build_cli_pool(raw_config, agent_configs)

        # Assert
        assert result is None, (
            "build_cli_pool must return None when no agent has backend='claude-cli'"
        )


# ---------------------------------------------------------------------------
# test_register_agents_calls_hub_register_for_each
# ---------------------------------------------------------------------------


class TestRegisterAgents:
    def test_register_agents_calls_hub_register_for_each(self) -> None:
        """register_agents calls hub.register_agent once per resolved agent."""
        # Arrange
        circuit_registry = CircuitRegistry()
        circuit_registry.register(CircuitBreaker(name="claude-cli"))
        hub = Hub(circuit_registry=circuit_registry)

        agent_alpha = Agent(
            name="alpha",
            system_prompt="prompt",
            memory_namespace="test",
            llm_config=ModelConfig(backend="nats"),
        )
        agent_beta = Agent(
            name="beta",
            system_prompt="prompt",
            memory_namespace="test",
            llm_config=ModelConfig(backend="nats"),
        )
        agent_configs: dict[str, Agent] = {"alpha": agent_alpha, "beta": agent_beta}

        # Mock the two resolved AgentBase instances
        mock_alpha = MagicMock()
        mock_alpha.name = "alpha"
        mock_beta = MagicMock()
        mock_beta.name = "beta"

        fake_msg_manager = MagicMock()
        raw_config: dict = {}

        import lyra.bootstrap.factory.hub_builder as hub_builder_mod

        with (
            patch.object(hub, "register_agent") as mock_register,
            patch.object(
                hub_builder_mod,
                "_resolve_agents",
                return_value={"alpha": mock_alpha, "beta": mock_beta},
            ),
        ):
            # Act
            register_agents(
                hub=hub,
                agent_configs=agent_configs,
                cli_pool=None,
                circuit_registry=circuit_registry,
                msg_manager=fake_msg_manager,
                stt_service=None,
                tts_service=None,
                agent_store=None,
                raw_config=raw_config,
                nats_llm_driver=None,
            )

            # Assert — register_agent called once per agent
            assert mock_register.call_count == 2, (
                f"Expected 2 register_agent calls, got {mock_register.call_count}"
            )
