"""Tests for _create_agent with omp-rpc backend — T4 + T11.

T4 (updated): with no registry, _create_agent raises ValueError with the
              "requires a ProviderRegistry" message (backend is now known).
T11: with a registry containing "omp-rpc", _create_agent succeeds.

Source: src/factory/bootstrap/factory/agent_factory.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from factory.bootstrap.factory.agent_factory import CreateAgentDeps, _create_agent
from factory.llm.registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deps(backend: str) -> CreateAgentDeps:
    """Build a minimal CreateAgentDeps with the given backend key.

    _create_agent() reads only ``deps.config.llm_config.backend`` before
    raising for an unknown backend, so all other fields can remain at their
    defaults (None / SimpleAgent default).
    """
    mock_config = MagicMock()
    mock_config.llm_config.backend = backend
    return CreateAgentDeps(config=mock_config, cli_pool=None)


# ---------------------------------------------------------------------------
# T4 (updated) — omp-rpc with no registry raises ValueError (known backend)
# ---------------------------------------------------------------------------


class TestCreateAgentOmpRpc:
    def test_create_agent_omp_rpc_raises_before_wiring(self) -> None:
        """_create_agent raises ValueError for omp-rpc backend when no registry.

        Updated in T11: omp-rpc is now a handled backend. Without a
        ProviderRegistry the code raises ValueError("backend='omp-rpc' requires
        a ProviderRegistry..."), matching the nats backend pattern.
        """
        # Arrange
        deps = _make_deps("omp-rpc")

        # Act / Assert
        with pytest.raises(
            ValueError,
            match="backend='omp-rpc' requires a ProviderRegistry",
        ) as exc_info:
            _create_agent(deps)

        # Confirm the exact exception type (not KeyError or RuntimeError)
        assert type(exc_info.value) is ValueError

    # ---------------------------------------------------------------------------
    # T11 — omp-rpc success path with driver in registry
    # ---------------------------------------------------------------------------

    def test_create_agent_omp_rpc_with_driver_succeeds(self) -> None:
        """_create_agent returns an agent when omp-rpc is in the ProviderRegistry.

        Builds a ProviderRegistry with a MagicMock LlmProvider registered under
        "omp-rpc". The agent_cls is also a MagicMock to avoid real construction.
        """
        # Arrange
        mock_provider = MagicMock()
        registry = ProviderRegistry()
        registry.register("omp-rpc", mock_provider)

        mock_config = MagicMock()
        mock_config.llm_config.backend = "omp-rpc"

        mock_agent_cls = MagicMock(return_value=MagicMock())

        deps = CreateAgentDeps(
            config=mock_config,
            cli_pool=None,
            provider_registry=registry,
            agent_cls=mock_agent_cls,
            session_tools=MagicMock(),  # skip real SessionTools construction
        )

        # Act
        result = _create_agent(deps)

        # Assert — agent_cls was called once, result is the mock agent instance
        mock_agent_cls.assert_called_once()
        assert result is mock_agent_cls.return_value

        # Confirm the provider passed into agent_cls is the omp-rpc one
        call_args = mock_agent_cls.call_args
        assert call_args.args[1] is mock_provider
