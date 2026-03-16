"""Bootstrap agent factory — provider registry, agent creation, and resolution."""

from __future__ import annotations

import dataclasses
import logging
import os

from lyra.agents.simple_agent import SimpleAgent
from lyra.config import DiscordBotConfig, TelegramBotConfig
from lyra.core.agent import Agent, AgentBase
from lyra.core.agent_config import AgentSTTConfig, AgentTTSConfig, SmartRoutingConfig
from lyra.core.agent_store import AgentStore
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.messages import MessageManager
from lyra.llm.base import LlmProvider
from lyra.llm.registry import ProviderRegistry
from lyra.llm.smart_routing import SmartRoutingDecorator
from lyra.stt import STTConfig, STTService
from lyra.tts import TTSConfig, TTSService

log = logging.getLogger(__name__)


def apply_agent_tts_overlay(
    agent_tts: AgentTTSConfig | None,
    tts_cfg: TTSConfig,
) -> TTSConfig:
    """Overlay non-None fields from AgentTTSConfig onto TTSConfig.

    None fields in agent_tts are skipped — voicecli global defaults remain in effect.
    Returns an updated TTSConfig (via dataclasses.replace).
    """
    if agent_tts is None:
        return tts_cfg
    a = agent_tts
    if a.engine is not None:
        tts_cfg = dataclasses.replace(tts_cfg, engine=a.engine)
    if a.voice is not None:
        tts_cfg = dataclasses.replace(tts_cfg, voice=a.voice)
    if a.language is not None:
        tts_cfg = dataclasses.replace(tts_cfg, language=a.language)
    return tts_cfg


def apply_agent_stt_overlay(
    agent_stt: AgentSTTConfig | None,
    stt_cfg: STTConfig,
) -> STTConfig:
    """Overlay non-None fields from AgentSTTConfig onto STTConfig.

    None fields in agent_stt are skipped — voicecli global defaults remain in effect.
    Returns an updated STTConfig (via dataclasses.replace).
    """
    if agent_stt is None:
        return stt_cfg
    a = agent_stt
    if a.language_detection_threshold is not None:
        stt_cfg = dataclasses.replace(
            stt_cfg, language_detection_threshold=a.language_detection_threshold
        )
    if a.language_detection_segments is not None:
        stt_cfg = dataclasses.replace(
            stt_cfg, language_detection_segments=a.language_detection_segments
        )
    if a.language_fallback is not None:
        stt_cfg = dataclasses.replace(stt_cfg, language_fallback=a.language_fallback)
    return stt_cfg


def _build_shared_base_providers(
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
) -> dict[str, LlmProvider]:
    """Build shared driver instances that are safe to reuse across all agents.

    Returns a mapping of backend name to base LlmProvider (without per-agent
    decorators).  The returned providers are:

    - ``"claude-cli"``  — ``ClaudeCliDriver`` wrapping the shared ``CliPool``
    - ``"anthropic-sdk"`` — ``CircuitBreakerDecorator`` -> ``RetryDecorator``
      -> ``AnthropicSdkDriver`` (no ``SmartRoutingDecorator``; that is
      per-agent and layered on top by ``_build_per_agent_registry``).

    Callers that need smart-routing must wrap the returned provider in a
    ``SmartRoutingDecorator`` before registering it in the per-agent
    ``ProviderRegistry``.
    """
    from lyra.llm.decorators import CircuitBreakerDecorator, RetryDecorator

    providers: dict[str, LlmProvider] = {}

    if cli_pool is not None:
        from lyra.llm.drivers.cli import ClaudeCliDriver

        providers["claude-cli"] = ClaudeCliDriver(cli_pool)
        log.info("Shared base: built claude-cli driver")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        from lyra.llm.drivers.sdk import AnthropicSdkDriver

        sdk_driver = AnthropicSdkDriver(api_key)
        retry: LlmProvider = RetryDecorator(sdk_driver, max_retries=3, backoff_base=1.0)

        anthropic_cb = circuit_registry.get("anthropic")
        if anthropic_cb is not None:
            providers["anthropic-sdk"] = CircuitBreakerDecorator(retry, anthropic_cb)
        else:
            providers["anthropic-sdk"] = retry
        log.info("Shared base: built anthropic-sdk driver (decorated)")

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
) -> tuple[ProviderRegistry, SmartRoutingDecorator | None]:
    """Build and return a ProviderRegistry with all configured drivers.

    Convenience wrapper used by the legacy single-agent bootstrap path.
    For multi-agent startup use ``_build_shared_base_providers`` +
    ``_build_per_agent_registry`` to avoid rebuilding the driver stack per
    agent.

    Returns (registry, smart_routing_decorator_or_None) so the routing
    decorator's history is accessible to the /routing admin command.
    """
    shared = _build_shared_base_providers(circuit_registry, cli_pool)
    return _build_per_agent_registry(shared, smart_routing_config)


def _create_agent(  # noqa: PLR0913 — factory with optional overrides for each agent dependency
    config: Agent,
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry | None = None,
    admin_user_ids: set[str] | None = None,
    msg_manager: MessageManager | None = None,
    stt: STTService | None = None,
    tts: TTSService | None = None,
    provider_registry: ProviderRegistry | None = None,
    smart_routing_decorator: SmartRoutingDecorator | None = None,
) -> AgentBase:
    """Select agent implementation based on backend config."""
    backend = config.model_config.backend
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
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt,
            tts=tts,
            smart_routing_decorator=smart_routing_decorator,
        )
    if backend in ("claude-cli", "ollama"):
        if cli_pool is None:
            raise RuntimeError(f"CliPool required for {backend} backend")
        if provider_registry is not None:
            provider = provider_registry.get("claude-cli")
        else:
            from lyra.llm.drivers.cli import ClaudeCliDriver

            provider = ClaudeCliDriver(cli_pool)
        return SimpleAgent(
            config,
            provider,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt,
            tts=tts,
        )
    raise ValueError(f"Unknown backend: {backend}")


def _resolve_agents(  # noqa: PLR0913
    agent_configs: dict[str, Agent],
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry,
    admin_user_ids: set[str],
    msg_manager: MessageManager,
    stt_service: STTService | None,
    tts_service: TTSService | None = None,
) -> dict[str, AgentBase]:
    """Create all uniquely named agents referenced by bot configs.

    Builds the shared driver layer once (``_build_shared_base_providers``),
    then layers a per-agent ``ProviderRegistry`` (and ``SmartRoutingDecorator``
    when configured) on top so that each agent's routing settings are respected
    independently — without reconstructing the underlying SDK client or circuit
    breaker for every agent.

    Accepts pre-loaded agent configs to avoid duplicate I/O.
    Returns a dict mapping agent_name to AgentBase instance.
    """
    # Build shared driver instances once — AnthropicSdkDriver, RetryDecorator,
    # CircuitBreakerDecorator and ClaudeCliDriver are stateless across agents.
    shared_providers = _build_shared_base_providers(circuit_registry, cli_pool)

    agents: dict[str, AgentBase] = {}
    for name, agent_config in sorted(agent_configs.items()):  # deterministic log order
        log.info(
            "Agent loaded: name=%s model=%s backend=%s",
            agent_config.name,
            agent_config.model_config.model,
            agent_config.model_config.backend,
        )
        # Layer per-agent decorators (SmartRoutingDecorator) on top of the
        # shared base so each agent's routing config is applied independently.
        sr_config = agent_config.smart_routing
        if (
            sr_config is not None
            and sr_config.enabled
            and agent_config.model_config.backend != "anthropic-sdk"
        ):
            log.warning(
                "agent %r: smart_routing.enabled=true but backend=%r — "
                "smart routing only applies to anthropic-sdk",
                name,
                agent_config.model_config.backend,
            )
        per_agent_registry, per_agent_routing = _build_per_agent_registry(
            shared_providers, smart_routing_config=sr_config
        )
        agent = _create_agent(
            agent_config,
            cli_pool,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt_service,
            tts=tts_service,
            provider_registry=per_agent_registry,
            smart_routing_decorator=per_agent_routing,
        )
        agents[name] = agent
    return agents


async def _resolve_bot_agent_map(
    agent_store: AgentStore,
    tg_bots: "list[TelegramBotConfig]",
    dc_bots: "list[DiscordBotConfig]",
) -> "dict[tuple[str, str], str]":
    """Resolve (platform, bot_id) -> agent_name for all configured bots.

    Resolution order per bot:
      1. bot_agent_map DB row (agent_store.get_bot_agent)
      2. bot_cfg.agent from TOML — auto-seeds bot_agent_map in DB
      3. Neither -> log error, skip (bot not included in result)

    Bots whose resolved agent name is not found in the agents table are also
    logged and skipped (adapter cannot be wired without a valid agent row).

    Returns dict mapping (platform, bot_id) -> agent_name for bots that can
    be safely wired.
    """
    result: dict[tuple[str, str], str] = {}

    all_bots: list[tuple[str, str, str | None]] = []
    for bot_cfg in tg_bots:
        toml_agent = getattr(bot_cfg, "agent", None)
        all_bots.append(("telegram", bot_cfg.bot_id, toml_agent))
    for bot_cfg in dc_bots:
        toml_agent = getattr(bot_cfg, "agent", None)
        all_bots.append(("discord", bot_cfg.bot_id, toml_agent))

    for platform, bot_id, toml_agent in all_bots:
        # 1. Check DB cache (bot_agent_map table)
        db_agent_name = agent_store.get_bot_agent(platform, bot_id)

        if db_agent_name is not None:
            # DB row found — validate the referenced agent exists in agents table.
            # A stale bot_agent_map row (agent deleted) is a configuration error.
            if agent_store.get(db_agent_name) is None:
                log.error(
                    "bot_agent_map: agent %r for (%r, %r) not found in"
                    " agents table — skipping adapter",
                    db_agent_name,
                    platform,
                    bot_id,
                )
                continue
            result[(platform, bot_id)] = db_agent_name
        elif toml_agent:
            # Check agent exists in DB before seeding the mapping
            if agent_store.get(toml_agent) is None:
                log.error(
                    "bot_agent_map: TOML agent %r for (%r, %r) not found in agents DB "
                    "— skipping adapter (run 'lyra agent init' to import TOMLs)",
                    toml_agent,
                    platform,
                    bot_id,
                )
                continue
            # 2. No DB row — fall back to TOML bot_cfg.agent, seed bot_agent_map.
            log.info(
                "bot_agent_map: no DB row for (%r, %r) — seeding from TOML agent=%r",
                platform,
                bot_id,
                toml_agent,
            )
            try:
                await agent_store.set_bot_agent(platform, bot_id, toml_agent)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "bot_agent_map: failed to seed (%r, %r) -> %r: %s",
                    platform,
                    bot_id,
                    toml_agent,
                    exc,
                )
            result[(platform, bot_id)] = toml_agent
        else:
            # 3. No DB row, no TOML agent — skip
            log.error(
                "bot_agent_map: no DB row and no TOML agent for"
                " (%r, %r) — skipping adapter",
                platform,
                bot_id,
            )
            continue

    return result
