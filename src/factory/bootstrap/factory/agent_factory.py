"""Bootstrap agent factory — provider registry, agent creation, and resolution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

# Re-exported for backward compatibility (tests import these from agent_factory)
from factory.agents.simple_agent import SimpleAgent
from factory.bootstrap.factory.bot_agent_map import (
    _resolve_bot_agent_map,
    resolve_bot_agent_map,  # noqa: F401 — DEBT:re-export-init  # type: ignore
)
from factory.bootstrap.factory.config import (
    LlmConfig,
    _build_agent_overrides,
    _load_circuit_config,
    _load_messages,
)
from factory.bootstrap.factory.providers import (
    _build_per_agent_registry,
    _build_shared_base_providers,
)
from factory.bootstrap.types import BotAuthBundle
from factory.bootstrap.wiring.auth import BotAuthDeps, _build_bot_auths
from factory.config import multibot_config_from_store
from factory.core.agent import Agent, AgentBase
from factory.core.agent.agent_loader import agent_row_to_config
from factory.core.cli.cli_pool import CliPool
from factory.core.lifecycle.circuit_breaker import CircuitRegistry
from factory.core.messaging.messages import MessageManager
from factory.core.ports.stt import STTProtocol
from factory.core.ports.tts import TtsProtocol
from factory.infrastructure.soul.soul_ops import preload_soul_caches_for_rows
from factory.infrastructure.stores.registry.agent_store import AgentStore
from factory.integrations.base import SessionTools
from factory.integrations.cortex_vault import CortexVault
from factory.integrations.web_intel import WebIntelScraper
from factory.llm.drivers.cli import ClaudeCliDriver
from factory.llm.registry import ProviderRegistry

if TYPE_CHECKING:
    from factory.bootstrap.bootstrap_stores import StoreBundle
    from factory.core.ports.blobstore import BlobStorePort
    from factory.llm.base import LlmProvider
    from factory.llm.llm_client import LlmClient

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
    cli_nats_driver: "LlmProvider | None" = None
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
    cli_nats_driver: "LlmProvider | None" = None
    omp_rpc_driver: "LlmProvider | None" = None


async def _init_bot_auths_and_agents(
    stores: StoreBundle,
    raw_config: dict,
    *,
    blob_store: "BlobStorePort | None" = None,
) -> BotAuthBundle:
    """Resolve multibot config, build authenticators, load agent configs."""
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)

    tg_multi_cfg, dc_multi_cfg = multibot_config_from_store(stores.bot)

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
        raise ValueError(
            "No bots configured — the runtime roster is sourced from BotStore."
            " Run 'factory bot init' to seed it from config.toml,"
            " then 'factory agent telegram list' to verify."
        )

    bot_agent_map = await _resolve_bot_agent_map(
        stores.agent, tg_multi_cfg.bots, dc_multi_cfg.bots
    )
    agent_names: set[str] = set(bot_agent_map.values())

    rows = []
    for n in sorted(agent_names):
        row = stores.agent.get(n)
        if row is not None:
            rows.append(row)
        else:
            log.error("Agent %r not found in DB — skipping", n)

    await preload_soul_caches_for_rows(rows, blob_store)

    agent_configs: dict[str, Agent] = {}
    for row in rows:
        overrides = _build_agent_overrides(raw_config, row.name)
        agent_configs[row.name] = agent_row_to_config(
            row,
            instance_overrides=overrides.model_dump(),
        )
    if not agent_configs:
        raise ValueError(
            "No agent configs could be loaded — run"
            " 'factory agent init' to seed the agents table"
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


def _create_agent(deps: CreateAgentDeps) -> AgentBase:  # noqa: C901 — 3-branch backend dispatch
    """Select agent implementation based on backend config."""
    backend = deps.config.llm_config.backend
    if backend in ("claude-cli", "nats", "omp-rpc"):
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
        elif backend == "omp-rpc":
            if deps.provider_registry is None:
                raise ValueError(
                    "backend='omp-rpc' requires a ProviderRegistry with"
                    " 'omp-rpc' registered. Is NATS_URL set?"
                )
            try:
                provider = deps.provider_registry.get("omp-rpc")
            except KeyError as exc:
                raise RuntimeError(
                    "backend='omp-rpc' registered but OmpRpcDriver missing from"
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
                    scraper=WebIntelScraper(), vault=CortexVault()
                )
            except (OSError, ImportError, RuntimeError, ValueError):  # <issue:1639>
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
            provider_registry=deps.provider_registry,
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
        omp_rpc_driver=deps.omp_rpc_driver,
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
