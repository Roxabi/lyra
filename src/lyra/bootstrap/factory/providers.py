"""Provider registry builders — shared driver construction for agents."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.bootstrap.factory.config import LlmConfig
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli.cli_pool import CliPool
from lyra.llm.base import LlmProvider
from lyra.llm.decorators import CircuitBreakerDecorator, RetryDecorator
from lyra.llm.drivers.cli import ClaudeCliDriver
from lyra.llm.registry import ProviderRegistry

if TYPE_CHECKING:
    from lyra.llm.llm_client import LlmClient

log = logging.getLogger(__name__)


def _build_shared_base_providers(  # noqa: PLR0913
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
    llm_cfg: LlmConfig,
    *,
    nats_llm_client: LlmClient | None = None,
    cli_nats_driver: LlmClient | None = None,
    cb_decorator_cls: type = CircuitBreakerDecorator,
    retry_decorator_cls: type = RetryDecorator,
) -> dict[str, LlmProvider]:
    """Build ``{backend: base LlmProvider}`` reusable across all agents.

    ``claude-cli`` (ClaudeCliDriver or LlmClient via clipool), ``nats`` (Retry ->
    LlmClient, only when ``nats_llm_client`` is provided). Callers layer
    decorators per agent via ``_build_per_agent_registry``.

    ``cli_nats_driver`` takes precedence over ``cli_pool`` for the
    ``claude-cli`` backend when both are provided.
    """
    providers: dict[str, LlmProvider] = {}

    if cli_nats_driver is not None:
        cli_cb = circuit_registry.get("claude-cli")
        base: LlmProvider = cli_nats_driver
        if cli_cb is not None:
            providers["claude-cli"] = cb_decorator_cls(base, cli_cb)
        else:
            providers["claude-cli"] = base
        log.info("Shared base: built claude-cli driver via NATS (decorated)")
    elif cli_pool is not None:
        cli_driver: LlmProvider = ClaudeCliDriver(cli_pool)
        cli_cb = circuit_registry.get("claude-cli")
        if cli_cb is not None:
            providers["claude-cli"] = cb_decorator_cls(cli_driver, cli_cb)
        else:
            providers["claude-cli"] = cli_driver
        log.info("Shared base: built claude-cli driver (in-process, decorated)")

    if nats_llm_client is not None:
        providers["nats"] = retry_decorator_cls(
            nats_llm_client,
            max_retries=llm_cfg.max_retries,
            backoff_base=llm_cfg.backoff_base,
        )
        log.info("Shared base: registered nats driver (decorated)")

    return providers


def _build_per_agent_registry(
    shared_providers: dict[str, LlmProvider],
) -> ProviderRegistry:
    """Build a per-agent ProviderRegistry on top of shared driver instances.

    ``shared_providers`` is the dict returned by ``_build_shared_base_providers``.
    For each backend, the provider is registered as-is.
    """
    registry = ProviderRegistry()

    for backend, base_provider in shared_providers.items():
        registry.register(backend, base_provider)

    return registry


def _build_provider_registry(
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
    llm_cfg: LlmConfig | None = None,  # None -> LlmConfig() (defaults)
) -> ProviderRegistry:
    """Build and return a ProviderRegistry with all configured drivers.

    Convenience wrapper used by the legacy single-agent bootstrap path.
    For multi-agent startup use ``_build_shared_base_providers`` +
    ``_build_per_agent_registry`` to avoid rebuilding the driver stack per
    agent.
    """
    shared = _build_shared_base_providers(
        circuit_registry, cli_pool, llm_cfg or LlmConfig(), cli_nats_driver=None
    )
    return _build_per_agent_registry(shared)
