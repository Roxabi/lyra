"""Bootstrap agent factory — provider registry, agent creation, and resolution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

# Re-exported for backward compatibility (tests import these from agent_factory)
from lyra.agents.simple_agent import SimpleAgent
from lyra.bootstrap.factory.bot_agent_map import (
    resolve_bot_agent_map,  # noqa: F401 — DEBT:re-export-init
)
from lyra.bootstrap.factory.config import (
    LlmConfig,
    _build_agent_overrides,
    _load_circuit_config,
    _load_messages,
)
from lyra.bootstrap.types import BotAuthBundle
from lyra.bootstrap.wiring.bootstrap_wiring import BotAuthDeps, _build_bot_auths
from lyra.config import load_multibot_config
from lyra.core.agent import Agent, AgentBase
from lyra.core.agent.agent_loader import agent_row_to_config
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli.cli_pool import CliPool
from lyra.core.messaging.messages import MessageManager
from lyra.core.ports.stt import STTProtocol
from lyra.core.ports.tts import TtsProtocol
from lyra.infrastructure.stores.agent_store import AgentStore
from lyra.integrations.base import SessionTools
from lyra.integrations.vault_cli import VaultCli
from lyra.integrations.web_intel import WebIntelScraper
from lyra.llm.base import LlmProvider
from lyra.llm.decorators import CircuitBreakerDecorator, RetryDecorator
from lyra.llm.drivers.cli import ClaudeCliDriver
from lyra.llm.registry import ProviderRegistry

if TYPE_CHECKING:
    from lyra.bootstrap.bootstrap_stores import StoreBundle
    from lyra.llm.llm_client import LlmClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DI containers
# ---------------------------------------------------------------------------


@dataclass
class CreateAgentDeps:
    config: Agent
    cli_pool: CliPool | None
    circuit_registry: CircuitRegistry | None = None
    msg_manager: MessageManager | None = None
    stt: STTProtocol | None = None
    tts: TtsProtocol | None = None
    provider_registry: ProviderRegistry | None = None
    agent_store: AgentStore | None = None
    cli_nats_driver: "LlmClient | None" = None
    agent_cls: Callable[..., AgentBase] = SimpleAgent
    cli_driver_cls: type = ClaudeCliDriver
    session_tools: SessionTools | None = None


@dataclass
class ResolveAgentsDeps:
    agent_configs: dict[str, Agent]
    cli_pool: CliPool | None
    circuit_registry: CircuitRegistry
    msg_manager: MessageManager
    stt_service: STTProtocol | None
    tts_service: TtsProtocol | None = None
    agent_store: AgentStore | None = None
    llm_cfg: LlmConfig | None = None
    nats_llm_client: "LlmClient | None" = None
    cli_nats_driver: "LlmClient | None" = None


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


async def _init_bot_auths_and_agents(
    stores: StoreBundle,
    raw_config: dict,
) -> BotAuthBundle:
    """Resolve multibot config, build authenticators, load agent configs."""
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)

    try:
        tg_multi_cfg, dc_multi_cfg = load_multibot_config(raw_config)
    except ValueError as exc:
        raise SystemExit(str(exc))

    tg_bot_auths, dc_bot_auths = _build_bot_auths(
        BotAuthDeps(
            bot_store=stores.bot,
            tg_multi_cfg=tg_multi_cfg,
            dc_multi_cfg=dc_multi_cfg,
            auth_store=stores.auth,
            admin_user_ids=admin_user_ids,
            alias_store=stores.identity_alias,
        )
    )
    log.info(
        "Authenticator: %d admin_user_id(s) configured",
        len(admin_user_ids),
    )

    if not tg_bot_auths and not dc_bot_auths:
        raise SystemExit(
            "No adapters configured — add at least one"
            " [[telegram.bots]] or [[discord.bots]] entry"
            " and run 'lyra bot init' to seed the bot store"
        )

    bot_agent_map = await _resolve_bot_agent_map(
        stores.agent, tg_multi_cfg.bots, dc_multi_cfg.bots
    )
    agent_names: set[str] = set(bot_agent_map.values())

    agent_configs: dict[str, Agent] = {}
    for n in sorted(agent_names):
        row = stores.agent.get(n)
        if row is not None:
            overrides = _build_agent_overrides(raw_config, n)
            agent_configs[n] = agent_row_to_config(
                row,
                instance_overrides=overrides.model_dump(),
            )
        else:
            log.error("Agent %r not found in DB — skipping", n)
    if not agent_configs:
        raise SystemExit(
            "No agent configs could be loaded — run"
            " 'lyra agent init' to seed the agents table"
        )

    first_agent_name = next(iter(sorted(agent_configs)))
    first_agent_config = agent_configs[first_agent_name]
    msg_manager = _load_messages(language=first_agent_config.i18n_language)

    return BotAuthBundle(
        tg_bot_auths=tg_bot_auths,
        dc_bot_auths=dc_bot_auths,
        bot_agent_map=bot_agent_map,
        agent_configs=agent_configs,
        first_agent_config=first_agent_config,
        msg_manager=msg_manager,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
    )


def _build_shared_base_providers(  # noqa: PLR0913
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
    llm_cfg: LlmConfig,
    *,
    nats_llm_client: "LlmClient | None" = None,
    cli_nats_driver: "LlmClient | None" = None,
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


def _create_agent(deps: CreateAgentDeps) -> AgentBase:
    """Select agent implementation based on backend config."""
    backend = deps.config.llm_config.backend
    if backend in ("claude-cli", "nats"):
        if backend == "nats":
            if deps.provider_registry is None:
                raise ValueError(
                    "backend='nats' requires a ProviderRegistry with 'nats' registered."
                    " Is NATS_URL set?"
                )
            try:
                provider = deps.provider_registry.get("nats")
            except KeyError as exc:
                raise RuntimeError(
                    "backend='nats' registered but LlmClient missing from"
                    " registry -- is NATS_URL set and driver started?"
                ) from exc
        elif deps.provider_registry is not None:
            provider = deps.provider_registry.get("claude-cli")
        else:
            if deps.cli_pool is None:
                raise RuntimeError(f"CliPool required for {backend} backend")
            provider = deps.cli_driver_cls(deps.cli_pool)

        if deps.session_tools is None:
            try:
                session_tools = SessionTools(
                    scraper=WebIntelScraper(), vault=VaultCli()
                )
            except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
                log.warning(
                    "agent_factory: could not build SessionTools — passing None",
                    exc_info=True,
                )
                session_tools = None
        else:
            session_tools = deps.session_tools

        return deps.agent_cls(
            deps.config,
            provider,
            cli_pool=deps.cli_pool,
            circuit_registry=deps.circuit_registry,
            msg_manager=deps.msg_manager,
            stt=deps.stt,
            tts=deps.tts,
            agent_store=deps.agent_store,
            session_tools=session_tools,
            cli_nats_driver=deps.cli_nats_driver,
        )
    raise ValueError(f"Unknown backend: {backend}")


def _resolve_agents(deps: ResolveAgentsDeps) -> dict[str, AgentBase]:
    """Create all uniquely named agents referenced by bot configs.

    Builds the shared driver layer once (``_build_shared_base_providers``),
    then layers a per-agent ``ProviderRegistry`` on top so that each agent's
    settings are respected independently -- without reconstructing the underlying
    SDK client or circuit breaker for every agent.

    Accepts pre-loaded agent configs to avoid duplicate I/O.
    Returns a dict mapping agent_name to AgentBase instance.

    ``nats_llm_client`` -- if provided (NATS_URL set), registers the shared
    ``LlmClient`` as the ``"nats"`` backend. Must be started first.

    ``cli_nats_driver`` -- if provided, used as the ``claude-cli`` backend
    instead of an in-process ``CliPool``. Takes precedence over ``cli_pool``.
    """
    shared_providers = _build_shared_base_providers(
        deps.circuit_registry,
        deps.cli_pool,
        deps.llm_cfg or LlmConfig(),
        nats_llm_client=deps.nats_llm_client,
        cli_nats_driver=deps.cli_nats_driver,
    )

    agents: dict[str, AgentBase] = {}
    for name, agent_config in sorted(  # deterministic log order
        deps.agent_configs.items()
    ):
        log.info(
            "Agent loaded: name=%s model=%s backend=%s",
            agent_config.name,
            agent_config.llm_config.model,
            agent_config.llm_config.backend,
        )
        per_agent_registry = _build_per_agent_registry(shared_providers)
        agent = _create_agent(
            CreateAgentDeps(
                config=agent_config,
                cli_pool=deps.cli_pool,
                circuit_registry=deps.circuit_registry,
                msg_manager=deps.msg_manager,
                stt=deps.stt_service,
                tts=deps.tts_service,
                provider_registry=per_agent_registry,
                agent_store=deps.agent_store,
                cli_nats_driver=deps.cli_nats_driver,
            )
        )
        agents[name] = agent
    return agents
