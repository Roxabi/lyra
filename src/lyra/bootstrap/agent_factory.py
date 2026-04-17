"""Bootstrap agent factory — provider registry, agent creation, and resolution."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

# Re-exported for backward compatibility (tests import these from agent_factory)
from lyra.bootstrap.bot_agent_map import resolve_bot_agent_map  # noqa: F401
from lyra.bootstrap.config import LlmConfig
from lyra.core.agent import Agent, AgentBase
from lyra.core.agent_config import SmartRoutingConfig
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.messages import MessageManager
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.llm.base import LlmProvider
from lyra.llm.registry import ProviderRegistry
from lyra.llm.smart_routing import SmartRoutingDecorator
from lyra.stt import STTProtocol
from lyra.tts import TtsProtocol

if TYPE_CHECKING:
    from lyra.llm.drivers.nats_driver import NatsLlmDriver

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backward-compatible alias used by multibot.py
# ---------------------------------------------------------------------------


async def _resolve_bot_agent_map(
    agent_store: AgentStore,
    tg_bots: list,
    dc_bots: list,
) -> dict:
    """Thin alias — delegates to bot_agent_map.resolve_bot_agent_map."""
    return await resolve_bot_agent_map(agent_store, tg_bots, dc_bots)


def _build_shared_base_providers(
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
    llm_cfg: LlmConfig,
    *,
    nats_llm_driver: "NatsLlmDriver | None" = None,
) -> dict[str, LlmProvider]:
    """Build ``{backend: base LlmProvider}`` reusable across all agents.

    ``claude-cli`` (ClaudeCliDriver), ``anthropic-sdk`` (CircuitBreaker ->
    Retry -> AnthropicSdkDriver), ``nats`` (Retry -> NatsLlmDriver, only when
    ``nats_llm_driver`` is provided). Callers layer ``SmartRoutingDecorator``
    per agent via ``_build_per_agent_registry``.
    """
    from lyra.llm.decorators import CircuitBreakerDecorator, RetryDecorator

    providers: dict[str, LlmProvider] = {}

    if cli_pool is not None:
        from lyra.llm.drivers.cli import ClaudeCliDriver

        cli_driver: LlmProvider = ClaudeCliDriver(cli_pool)
        cli_cb = circuit_registry.get("claude-cli")
        if cli_cb is not None:
            providers["claude-cli"] = CircuitBreakerDecorator(cli_driver, cli_cb)
        else:
            providers["claude-cli"] = cli_driver
        log.info("Shared base: built claude-cli driver (decorated)")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        from lyra.llm.drivers.sdk import AnthropicSdkDriver

        sdk_driver = AnthropicSdkDriver(api_key)
        retry: LlmProvider = RetryDecorator(
            sdk_driver,
            max_retries=llm_cfg.max_retries,
            backoff_base=llm_cfg.backoff_base,
        )

        anthropic_cb = circuit_registry.get("anthropic")
        if anthropic_cb is not None:
            providers["anthropic-sdk"] = CircuitBreakerDecorator(retry, anthropic_cb)
        else:
            providers["anthropic-sdk"] = retry
        log.info("Shared base: built anthropic-sdk driver (decorated)")

    if nats_llm_driver is not None:
        providers["nats"] = RetryDecorator(
            nats_llm_driver,
            max_retries=llm_cfg.max_retries,
            backoff_base=llm_cfg.backoff_base,
        )
        log.info("Shared base: registered nats driver (decorated)")

    return providers


def _build_per_agent_registry(
    shared_providers: dict[str, LlmProvider],
    smart_routing_config: SmartRoutingConfig | None = None,
) -> tuple[ProviderRegistry, SmartRoutingDecorator | None]:
    """Build a per-agent ProviderRegistry on top of shared driver instances.

    ``shared_providers`` is the dict returned by ``_build_shared_base_providers``.
    For each backend, the provider is registered as-is unless
    ``smart_routing_config`` is set *and* the backend is ``"anthropic-sdk"``,
    in which case a fresh ``SmartRoutingDecorator`` wraps the shared base
    (preserving per-agent routing history and config).

    Stack (when smart routing enabled):
        CircuitBreaker -> SmartRouting -> Retry -> Driver

    Returns ``(registry, routing_decorator_or_None)``.
    """
    registry = ProviderRegistry()
    routing_decorator: SmartRoutingDecorator | None = None

    for backend, base_provider in shared_providers.items():
        if backend == "anthropic-sdk" and smart_routing_config is not None:
            from lyra.llm.smart_routing import SmartRoutingDecorator as SRD

            # SmartRouting wraps the base (CB -> SmartRouting -> Retry -> Driver).
            # The CB is already baked into base_provider; SRD sits on top.
            routing_decorator = SRD(base_provider, smart_routing_config)
            registry.register(backend, routing_decorator)
        else:
            registry.register(backend, base_provider)

    return registry, routing_decorator


def _build_provider_registry(
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
    smart_routing_config: SmartRoutingConfig | None = None,
    llm_cfg: LlmConfig | None = None,  # None → LlmConfig() (defaults)
) -> tuple[ProviderRegistry, SmartRoutingDecorator | None]:
    """Build and return a ProviderRegistry with all configured drivers.

    Convenience wrapper used by the legacy single-agent bootstrap path.
    For multi-agent startup use ``_build_shared_base_providers`` +
    ``_build_per_agent_registry`` to avoid rebuilding the driver stack per
    agent.

    Returns (registry, smart_routing_decorator_or_None) so the routing
    decorator's history is accessible to the /routing admin command.
    """
    shared = _build_shared_base_providers(
        circuit_registry, cli_pool, llm_cfg or LlmConfig()
    )
    return _build_per_agent_registry(shared, smart_routing_config)


def _create_agent(  # noqa: PLR0913 — factory with optional overrides for each agent dependency
    config: Agent,
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry | None = None,
    msg_manager: MessageManager | None = None,
    stt: STTProtocol | None = None,
    tts: TtsProtocol | None = None,
    provider_registry: ProviderRegistry | None = None,
    smart_routing_decorator: SmartRoutingDecorator | None = None,
    agent_store: AgentStore | None = None,
) -> AgentBase:
    """Select agent implementation based on backend config."""
    backend = config.llm_config.backend
    if backend == "anthropic-sdk":
        from lyra.agents.anthropic_agent import AnthropicAgent

        if provider_registry is not None:
            provider = provider_registry.get("anthropic-sdk")
        else:
            from lyra.llm.drivers.sdk import AnthropicSdkDriver

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise SystemExit("Missing required env var: ANTHROPIC_API_KEY")
            provider = AnthropicSdkDriver(api_key)
        return AnthropicAgent(
            config,
            provider,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            stt=stt,
            tts=tts,
            smart_routing_decorator=smart_routing_decorator,
            agent_store=agent_store,
        )
    if backend in ("claude-cli", "ollama", "nats"):
        if backend == "nats":
            if provider_registry is None:
                raise ValueError(
                    "backend='nats' requires a ProviderRegistry with 'nats' registered."
                    " Is NATS_URL set?"
                )
            try:
                provider = provider_registry.get("nats")
            except KeyError as exc:
                raise RuntimeError(
                    "backend='nats' registered but NatsLlmDriver missing from"
                    " registry — is NATS_URL set and driver started?"
                ) from exc
        elif provider_registry is not None:
            provider = provider_registry.get("claude-cli")
        else:
            if cli_pool is None:
                raise RuntimeError(f"CliPool required for {backend} backend")
            from lyra.llm.drivers.cli import ClaudeCliDriver

            provider = ClaudeCliDriver(cli_pool)
        from lyra.agents.simple_agent import SimpleAgent

        return SimpleAgent(
            config,
            provider,
            cli_pool=cli_pool,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            stt=stt,
            tts=tts,
            agent_store=agent_store,
        )
    raise ValueError(f"Unknown backend: {backend}")


def _resolve_agents(  # noqa: PLR0913
    agent_configs: dict[str, Agent],
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
    stt_service: STTProtocol | None,
    tts_service: TtsProtocol | None = None,
    agent_store: AgentStore | None = None,
    llm_cfg: LlmConfig | None = None,
    nats_llm_driver: "NatsLlmDriver | None" = None,
) -> dict[str, AgentBase]:
    """Create all uniquely named agents referenced by bot configs.

    Builds the shared driver layer once (``_build_shared_base_providers``),
    then layers a per-agent ``ProviderRegistry`` (and ``SmartRoutingDecorator``
    when configured) on top so that each agent's routing settings are respected
    independently — without reconstructing the underlying SDK client or circuit
    breaker for every agent.

    Accepts pre-loaded agent configs to avoid duplicate I/O.
    Returns a dict mapping agent_name to AgentBase instance.

    ``nats_llm_driver`` — if provided (NATS_URL set), registers the shared
    ``NatsLlmDriver`` as the ``"nats"`` backend. Must be started first.
    """
    shared_providers = _build_shared_base_providers(
        circuit_registry,
        cli_pool,
        llm_cfg or LlmConfig(),
        nats_llm_driver=nats_llm_driver,
    )

    agents: dict[str, AgentBase] = {}
    for name, agent_config in sorted(agent_configs.items()):  # deterministic log order
        log.info(
            "Agent loaded: name=%s model=%s backend=%s",
            agent_config.name,
            agent_config.llm_config.model,
            agent_config.llm_config.backend,
        )
        # Layer per-agent decorators (SmartRoutingDecorator) on top of the
        # shared base so each agent's routing config is applied independently.
        sr_config = agent_config.smart_routing
        if (
            sr_config is not None
            and sr_config.enabled
            and agent_config.llm_config.backend != "anthropic-sdk"
        ):
            log.warning(
                "agent %r: smart_routing.enabled=true but backend=%r — "
                "smart routing only applies to anthropic-sdk",
                name,
                agent_config.llm_config.backend,
            )
        per_agent_registry, per_agent_routing = _build_per_agent_registry(
            shared_providers, smart_routing_config=sr_config
        )
        agent = _create_agent(
            agent_config,
            cli_pool,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            stt=stt_service,
            tts=tts_service,
            provider_registry=per_agent_registry,
            smart_routing_decorator=per_agent_routing,
            agent_store=agent_store,
        )
        agents[name] = agent
    return agents
