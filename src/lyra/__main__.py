"""Entry point: python -m lyra.

Starts Telegram + Discord adapters in one event loop.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import re
import signal
import sys
import time
import tomllib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException

from lyra.adapters.discord import DiscordAdapter
from lyra.adapters.telegram import TelegramAdapter
from lyra.agents.simple_agent import SimpleAgent
from lyra.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
    load_multibot_config,
)
from lyra.core.admin import set_admin_user_ids
from lyra.core.agent import Agent, AgentBase, SmartRoutingConfig, load_agent_config
from lyra.core.auth import AuthMiddleware
from lyra.core.auth_store import AuthStore
from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.credential_store import CredentialStore, LyraKeyring
from lyra.core.hub import Hub, RoutingKey
from lyra.core.message import Platform
from lyra.core.messages import MessageManager
from lyra.core.outbound_dispatcher import OutboundDispatcher
from lyra.core.pairing import PairingConfig, PairingManager, set_pairing_manager
from lyra.errors import KeyringError, MissingCredentialsError
from lyra.llm.base import LlmProvider
from lyra.llm.registry import ProviderRegistry
from lyra.llm.smart_routing import SmartRoutingDecorator
from lyra.stt import STTService, load_stt_config
from lyra.tts import TTSService, load_tts_config

log = logging.getLogger(__name__)

_CB_DEFAULTS: dict[str, int] = {"failure_threshold": 5, "recovery_timeout": 60}
_CB_SERVICES = ("anthropic", "telegram", "discord", "hub")
_ADMIN_ID_PATTERN = re.compile(r"^(tg|dc):user:\d+$")


def _load_raw_config(config_path: str | None = None) -> dict:
    """Open and parse config.toml once; return the raw dict.

    Resolution order: $LYRA_CONFIG env var → 'config.toml' in cwd → empty dict.
    """
    raw: dict = {}
    path = config_path or os.environ.get("LYRA_CONFIG", "config.toml")
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        log.info("Loaded config from %s", path)
    except FileNotFoundError:
        log.info("No config file at %s — using defaults", path)
    return raw


def _build_agent_overrides(raw: dict, name: str) -> dict:
    """Build instance-level overrides for an agent from config.toml.

    Merges [defaults] with [agents.<name>], with agent-specific values winning.
    Provides machine-specific fallbacks for cwd, persona, and workspaces that
    are not versioned in agents/*.toml.
    """
    defaults: dict = raw.get("defaults", {})
    agent_specific: dict = raw.get("agents", {}).get(name, {})
    merged = {**defaults, **agent_specific}
    # workspaces need a deep merge: defaults provide base, agent-specific keys win
    ws_defaults = defaults.get("workspaces", {})
    ws_agent = agent_specific.get("workspaces", {})
    if ws_defaults or ws_agent:
        merged["workspaces"] = {**ws_defaults, **ws_agent}
    return merged


def _load_circuit_config(
    raw: dict,
) -> tuple[CircuitRegistry, set[str]]:
    """Load [circuit_breaker.*] and [admin] sections from raw config dict.

    Returns (CircuitRegistry with 4 named CBs, set of admin user_ids).
    Missing sections → all defaults.
    """
    cb_section = raw.get("circuit_breaker", {})
    registry = CircuitRegistry()
    for name in _CB_SERVICES:
        cfg: dict[str, int] = {**_CB_DEFAULTS, **cb_section.get(name, {})}
        registry.register(
            CircuitBreaker(
                name=name,
                failure_threshold=cfg["failure_threshold"],
                recovery_timeout=cfg["recovery_timeout"],
            )
        )

    admin_ids: set[str] = set(raw.get("admin", {}).get("user_ids", []))
    for aid in admin_ids:
        if not _ADMIN_ID_PATTERN.match(aid):
            log.warning(
                "Admin ID %r does not match expected format "
                "(tg|dc):user:<digits> — verify config.toml [admin].user_ids",
                aid,
            )
    return registry, admin_ids


def _load_pairing_config(raw: dict) -> PairingConfig:
    """Load [pairing] section from raw config dict. Missing section → all defaults."""
    pairing_section: dict = raw.get("pairing", {})
    return PairingConfig.from_dict(pairing_section)


def _load_messages(language: str = "en") -> MessageManager:
    """Load MessageManager.

    Resolution order:
      LYRA_MESSAGES_CONFIG env var → messages.toml in cwd → bundled config.
    """
    bundled = Path(__file__).resolve().parent / "config" / "messages.toml"
    path_str = os.environ.get("LYRA_MESSAGES_CONFIG") or (
        "messages.toml" if Path("messages.toml").exists() else str(bundled)
    )
    if not path_str.endswith(".toml"):
        log.warning(
            "LYRA_MESSAGES_CONFIG %r does not end with .toml — ignoring, using bundled",
            path_str,
        )
        path_str = str(bundled)
    log.info("Loaded messages from %s", path_str)
    return MessageManager(path_str, language=language)


def _build_provider_registry(
    circuit_registry: CircuitRegistry,
    cli_pool: CliPool | None,
    smart_routing_config: SmartRoutingConfig | None = None,
) -> tuple[ProviderRegistry, SmartRoutingDecorator | None]:
    """Build and return a ProviderRegistry with all configured drivers.

    Returns (registry, smart_routing_decorator_or_None) so the routing
    decorator's history is accessible to the /routing admin command.
    """
    from lyra.llm.decorators import CircuitBreakerDecorator, RetryDecorator

    registry = ProviderRegistry()
    routing_decorator: SmartRoutingDecorator | None = None

    if cli_pool is not None:
        from lyra.llm.drivers.cli import ClaudeCliDriver

        registry.register("claude-cli", ClaudeCliDriver(cli_pool))
        log.info("ProviderRegistry: registered claude-cli driver")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        from lyra.llm.drivers.sdk import AnthropicSdkDriver

        sdk_driver = AnthropicSdkDriver(api_key)
        retry = RetryDecorator(sdk_driver, max_retries=3, backoff_base=1.0)

        # Smart routing wraps retry (CB → SmartRouting → Retry → Driver)
        inner: LlmProvider = retry
        if smart_routing_config is not None:
            from lyra.llm.smart_routing import SmartRoutingDecorator as SRD

            routing_decorator = SRD(retry, smart_routing_config)
            inner = routing_decorator

        anthropic_cb = circuit_registry.get("anthropic")
        if anthropic_cb is not None:
            provider: LlmProvider = CircuitBreakerDecorator(inner, anthropic_cb)
        else:
            provider = inner
        registry.register("anthropic-sdk", provider)
        log.info("ProviderRegistry: registered anthropic-sdk driver (decorated)")

    return registry, routing_decorator


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


def create_health_app(hub: Hub) -> FastAPI:
    """Create a root FastAPI app with /health endpoint for hub monitoring.

    This is the top-level HTTP app — adapter sub-apps can be mounted on it.
    The /health endpoint exposes hub-level health without requiring adapter auth.
    """
    app = FastAPI(title="Lyra Hub")

    @app.get("/health")
    async def health(authorization: str = Header(default="")) -> dict:
        health_secret = os.environ.get("LYRA_HEALTH_SECRET", "")
        expected = f"Bearer {health_secret}"
        authenticated = bool(health_secret) and hmac.compare_digest(
            authorization, expected
        )

        # Security: wrong/missing token intentionally returns the minimal
        # response rather than 401, to avoid revealing whether a secret is
        # configured.  This differs from /config which returns 401.
        if not authenticated:
            return {"ok": True}

        uptime_s = time.monotonic() - hub._start_time

        last_message_age_s: float | None = None
        if hub._last_processed_at is not None:
            last_message_age_s = time.monotonic() - hub._last_processed_at

        circuits: dict[str, dict[str, object]] = {}
        if hub.circuit_registry is not None:
            all_status = hub.circuit_registry.get_all_status()
            circuits = {
                name: {
                    "state": s.state.value,
                    "retry_after": s.retry_after,
                }
                for name, s in all_status.items()
            }

        inbound: dict[str, int] = {
            p.value: hub.inbound_bus.qsize(p)
            for p in hub.inbound_bus.registered_platforms()
        }
        outbound: dict[str, int] = {
            platform.value: dispatcher.qsize()
            for (platform, _bot_id), dispatcher in hub.outbound_dispatchers.items()
        }

        return {
            "ok": True,
            "queue_size": hub.inbound_bus.staging_qsize(),
            "queues": {"inbound": inbound, "outbound": outbound},
            "last_message_age_s": last_message_age_s,
            "uptime_s": round(uptime_s, 1),
            "circuits": circuits,
        }

    @app.get("/config")
    async def config_endpoint(
        authorization: str = Header(default=""),
        agent: str = "lyra_default",
    ) -> dict:
        config_secret = os.environ.get("LYRA_CONFIG_SECRET", "")
        if not config_secret or not hmac.compare_digest(
            authorization, f"Bearer {config_secret}"
        ):
            raise HTTPException(status_code=401, detail="unauthorized")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent_obj = hub.agent_registry.get(agent)
        if not isinstance(agent_obj, AnthropicAgent):
            raise HTTPException(
                status_code=404,
                detail="runtime config not available for this agent backend",
            )
        rc = agent_obj.runtime_config
        return {
            "style": rc.style,
            "language": rc.language,
            "temperature": rc.temperature,
            "model": rc.model,
            "max_steps": rc.max_steps,
            "extra_instructions": rc.extra_instructions,
            "effective_model": rc.model or agent_obj.config.model_config.model,
            "effective_max_steps": (
                rc.max_steps or agent_obj.config.model_config.max_turns
            ),
        }

    return app


def _resolve_agents(  # noqa: PLR0913
    agent_configs: dict[str, Agent],
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry,
    admin_user_ids: set[str],
    msg_manager: MessageManager,
    stt_service: STTService | None,
) -> dict[str, AgentBase]:
    """Create all uniquely named agents referenced by bot configs.

    Builds a per-agent ProviderRegistry (and SmartRoutingDecorator when configured)
    so that each agent's routing settings are respected independently.
    Accepts pre-loaded agent configs to avoid duplicate I/O.
    Returns a dict mapping agent_name → AgentBase instance.
    """
    agents: dict[str, AgentBase] = {}
    for name, agent_config in sorted(agent_configs.items()):  # deterministic log order
        log.info(
            "Agent loaded: name=%s model=%s backend=%s",
            agent_config.name,
            agent_config.model_config.model,
            agent_config.model_config.backend,
        )
        # Build per-agent provider registry so each agent's smart_routing config
        # is applied independently (avoids silent cross-agent routing mis-dispatch).
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
        per_agent_registry, per_agent_routing = _build_provider_registry(
            circuit_registry, cli_pool, smart_routing_config=sr_config
        )
        agent = _create_agent(
            agent_config,
            cli_pool,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt_service,
            provider_registry=per_agent_registry,
            smart_routing_decorator=per_agent_routing,
        )
        agents[name] = agent
    return agents


async def _bootstrap_multibot(  # noqa: C901, PLR0915 — startup wiring: each adapter/service requires sequential conditional setup
    raw_config: dict,
    circuit_registry: CircuitRegistry,
    admin_user_ids: set[str],
    tg_multi_cfg: TelegramMultiConfig,
    dc_multi_cfg: DiscordMultiConfig,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire hub + adapters for the multi-bot configuration schema and run until stop.

    Handles [[telegram.bots]] / [[discord.bots]] arrays from config.toml.
    Each bot is wired individually with its own AuthMiddleware, OutboundDispatcher,
    and adapter.
    """
    # ------------------------------------------------------------------
    # Multi-bot path: validate auth per bot, collect unique agent names.
    # ------------------------------------------------------------------
    vault_dir_mb = Path.home() / ".lyra"
    vault_dir_mb.mkdir(parents=True, exist_ok=True)
    auth_store = AuthStore(db_path=vault_dir_mb / "auth.db")
    await auth_store.connect()
    keyring_mb = LyraKeyring.load_or_create(vault_dir_mb / "keyring.key")
    cred_store = CredentialStore(
        db_path=vault_dir_mb / "auth.db",
        keyring=keyring_mb,
    )
    await cred_store.connect()

    # Seed per-bot owner/trusted users as permanent grants
    auth_block: dict = raw_config.get("auth", {})
    for entry in auth_block.get("telegram_bots", []):
        synthetic = {"auth": {"telegram": entry}}
        await auth_store.seed_from_config(synthetic, "telegram")
    for entry in auth_block.get("discord_bots", []):
        synthetic = {"auth": {"discord": entry}}
        await auth_store.seed_from_config(synthetic, "discord")

    tg_bot_auths: list[tuple[TelegramBotConfig, AuthMiddleware]] = []
    dc_bot_auths: list[tuple[DiscordBotConfig, AuthMiddleware]] = []

    try:
        for bot_cfg in tg_multi_cfg.bots:
            auth = AuthMiddleware.from_bot_config(
                raw_config, "telegram", bot_cfg.bot_id, store=auth_store
            )
            if auth is None:
                log.warning(
                    "telegram bot_id=%r has no auth config — skipping",
                    bot_cfg.bot_id,
                )
                continue
            tg_bot_auths.append((bot_cfg, auth))

        for bot_cfg in dc_multi_cfg.bots:
            auth = AuthMiddleware.from_bot_config(
                raw_config, "discord", bot_cfg.bot_id, store=auth_store
            )
            if auth is None:
                log.warning(
                    "discord bot_id=%r has no auth config — skipping",
                    bot_cfg.bot_id,
                )
                continue
            dc_bot_auths.append((bot_cfg, auth))
    except ValueError as exc:
        sys.exit(str(exc))

    if not tg_bot_auths and not dc_bot_auths:
        sys.exit(
            "No adapters configured — add at least one [[telegram.bots]] or"
            " [[discord.bots]] entry with a matching [[auth.telegram_bots]] or"
            " [[auth.discord_bots]] section to config.toml"
        )

    # Collect unique agent names referenced across all bot configs
    agent_names: set[str] = set()
    for bot_cfg, _ in tg_bot_auths:
        agent_names.add(bot_cfg.agent)
    for bot_cfg, _ in dc_bot_auths:
        agent_names.add(bot_cfg.agent)

    # Load all agent configs once — used for cli_pool detection, smart routing,
    # msg_manager language, and agent creation. Avoids duplicate I/O and TOCTOU.
    agent_configs: dict[str, Agent] = {
        n: load_agent_config(
            n, instance_overrides=_build_agent_overrides(raw_config, n)
        )
        for n in sorted(agent_names)
    }
    first_agent_name = next(iter(sorted(agent_names)))
    first_agent_config = agent_configs[first_agent_name]

    # Use the first agent's language for messages (all agents share one msg_manager)
    msg_manager = _load_messages(language=first_agent_config.i18n_language)

    pairing_config = _load_pairing_config(raw_config)
    if pairing_config.enabled and not admin_user_ids:
        log.warning(
            "Pairing is enabled but no admin user_ids configured in "
            "[admin].user_ids — no one can run /invite or /unpair"
        )
    pm: PairingManager | None = None
    if pairing_config.enabled:
        pm = PairingManager(
            config=pairing_config,
            db_path=vault_dir_mb / "pairing.db",
            admin_user_ids=admin_user_ids,
            auth_store=auth_store,
        )
        await pm.connect()
        set_pairing_manager(pm)

    stt_service: STTService | None = None
    if os.environ.get("STT_MODEL_SIZE"):
        try:
            stt_cfg = load_stt_config()
            stt_service = STTService(stt_cfg)
            log.info("STT enabled: model=%s (via voiceCLI)", stt_cfg.model_size)
        except ValueError as exc:
            raise SystemExit(f"Invalid STT configuration: {exc}") from exc

    from lyra.core.debouncer import DEFAULT_DEBOUNCE_MS

    hub = Hub(
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        pairing_manager=pm,
        stt=stt_service,
        debounce_ms=DEFAULT_DEBOUNCE_MS,
    )

    # Build cli_pool if any agent needs it (uses pre-loaded configs)
    cli_pool: CliPool | None = None
    for cfg in agent_configs.values():
        if cfg.model_config.backend == "claude-cli":
            cli_pool = CliPool()
            await cli_pool.start()
            break

    # Create all unique agents — each gets its own ProviderRegistry built from
    # its own smart_routing config inside _resolve_agents().
    all_agents = _resolve_agents(
        agent_configs,
        cli_pool,
        circuit_registry,
        admin_user_ids,
        msg_manager,
        stt_service,
    )
    for ag in all_agents.values():
        hub.register_agent(ag)

    # Wire Telegram adapters
    tg_adapters: list[TelegramAdapter] = []
    tg_dispatchers: list[OutboundDispatcher] = []
    for bot_cfg, auth in tg_bot_auths:
        tg_creds = await cred_store.get_full("telegram", bot_cfg.bot_id)
        if tg_creds is None:
            raise MissingCredentialsError("telegram", bot_cfg.bot_id)
        tg_token, tg_webhook_secret = tg_creds
        adapter = TelegramAdapter(
            bot_id=bot_cfg.bot_id,
            token=tg_token,
            hub=hub,
            bot_username=bot_cfg.bot_username,
            webhook_secret=tg_webhook_secret or "",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auth=auth,
        )
        hub.register_adapter(Platform.TELEGRAM, bot_cfg.bot_id, adapter)
        tg_key = RoutingKey(Platform.TELEGRAM, bot_cfg.bot_id, "*")
        hub.register_binding(
            Platform.TELEGRAM,
            bot_cfg.bot_id,
            "*",
            bot_cfg.agent,
            tg_key.to_pool_id(),
        )
        dispatcher = OutboundDispatcher(
            platform_name="telegram",
            adapter=adapter,
            circuit=circuit_registry.get("telegram"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.TELEGRAM, bot_cfg.bot_id, dispatcher)
        tg_adapters.append(adapter)
        tg_dispatchers.append(dispatcher)
        log.info(
            "Registered Telegram bot bot_id=%r agent=%r",
            bot_cfg.bot_id,
            bot_cfg.agent,
        )

    # Wire Discord adapters
    dc_adapters: list[tuple[DiscordAdapter, DiscordBotConfig, str]] = []
    dc_dispatchers: list[OutboundDispatcher] = []
    for bot_cfg, auth in dc_bot_auths:
        dc_creds = await cred_store.get_full("discord", bot_cfg.bot_id)
        if dc_creds is None:
            raise MissingCredentialsError("discord", bot_cfg.bot_id)
        dc_token, _ = dc_creds
        adapter = DiscordAdapter(
            hub=hub,
            bot_id=bot_cfg.bot_id,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auto_thread=bot_cfg.auto_thread,
            auth=auth,
        )
        hub.register_adapter(Platform.DISCORD, bot_cfg.bot_id, adapter)
        dc_key = RoutingKey(Platform.DISCORD, bot_cfg.bot_id, "*")
        hub.register_binding(
            Platform.DISCORD,
            bot_cfg.bot_id,
            "*",
            bot_cfg.agent,
            dc_key.to_pool_id(),
        )
        dispatcher = OutboundDispatcher(
            platform_name="discord",
            adapter=adapter,
            circuit=circuit_registry.get("discord"),
            circuit_registry=circuit_registry,
            bot_id=bot_cfg.bot_id,
        )
        hub.register_outbound_dispatcher(Platform.DISCORD, bot_cfg.bot_id, dispatcher)
        dc_adapters.append((adapter, bot_cfg, dc_token))
        dc_dispatchers.append(dispatcher)
        log.info(
            "Registered Discord bot bot_id=%r agent=%r",
            bot_cfg.bot_id,
            bot_cfg.agent,
        )

    # Start buses and dispatchers
    await hub.inbound_bus.start()
    await hub.inbound_audio_bus.start()
    for d in tg_dispatchers:
        await d.start()
    for d in dc_dispatchers:
        await d.start()

    health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
    health_app = create_health_app(hub)
    health_config = uvicorn.Config(
        health_app, host="127.0.0.1", port=health_port, log_level="warning"
    )
    health_server = uvicorn.Server(health_config)

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        _loop = asyncio.get_running_loop()
        _loop.add_signal_handler(signal.SIGINT, stop.set)
        _loop.add_signal_handler(signal.SIGTERM, stop.set)

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(hub._audio_loop(), name="hub-audio"),
        asyncio.create_task(health_server.serve(), name="health"),
    ]
    for tg_adapter in tg_adapters:
        tasks.append(
            asyncio.create_task(
                tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
                name=f"telegram:{tg_adapter._bot_id}",
            )
        )
    for dc_adapter, dc_bot_cfg, dc_token in dc_adapters:
        tasks.append(
            asyncio.create_task(
                dc_adapter.start(dc_token),
                name=f"discord:{dc_bot_cfg.bot_id}",
            )
        )

    tg_active = [f"telegram:{a._bot_id}" for a in tg_adapters]
    dc_active = [f"discord:{c.bot_id}" for _, c, _ in dc_adapters]
    active = tg_active + dc_active
    log.info(
        "Lyra started — adapters: %s, health on :%d.",
        ", ".join(active) if active else "none",
        health_port,
    )

    await stop.wait()

    log.info("Shutdown signal received — stopping…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await hub.inbound_bus.stop()
    await hub.inbound_audio_bus.stop()
    for d in tg_dispatchers:
        await d.stop()
    for d in dc_dispatchers:
        await d.stop()
    for dc_adapter, _, _dc_tok in dc_adapters:
        await dc_adapter.close()
    if pm is not None:
        await pm.close()
    await cred_store.close()
    await auth_store.close()
    if cli_pool is not None:
        await cli_pool.stop()
    log.info("Lyra stopped.")


async def _bootstrap_legacy(  # noqa: C901, PLR0915 — startup wiring: each adapter/service requires sequential conditional setup
    raw_config: dict,
    circuit_registry: CircuitRegistry,
    admin_user_ids: set[str],
    *,
    adapter: str = "all",
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire hub + adapters for the legacy single-bot schema and run until stop.

    Handles [auth.telegram] / [auth.discord] flat sections from config.toml.
    Behavior is identical to the original single-bot implementation.
    """
    # ------------------------------------------------------------------
    # Legacy single-bot path (backward compat).
    # Runs when no [[telegram.bots]] / [[discord.bots]] arrays are found.
    # Behavior is identical to the original single-bot implementation.
    # ------------------------------------------------------------------
    vault_dir_lg = Path.home() / ".lyra"
    vault_dir_lg.mkdir(parents=True, exist_ok=True)
    auth_store_legacy = AuthStore(db_path=vault_dir_lg / "auth.db")
    await auth_store_legacy.connect()
    keyring_lg = LyraKeyring.load_or_create(vault_dir_lg / "keyring.key")
    cred_store_legacy = CredentialStore(
        db_path=vault_dir_lg / "auth.db",
        keyring=keyring_lg,
    )
    await cred_store_legacy.connect()
    await auth_store_legacy.seed_from_config(raw_config, "telegram")
    await auth_store_legacy.seed_from_config(raw_config, "discord")

    try:
        tg_auth = (
            None
            if adapter == "discord"
            else AuthMiddleware.from_config(
                raw_config, "telegram", store=auth_store_legacy
            )
        )
        dc_auth = (
            None
            if adapter == "telegram"
            else AuthMiddleware.from_config(
                raw_config, "discord", store=auth_store_legacy
            )
        )
    except ValueError as exc:
        sys.exit(str(exc))

    if tg_auth is None and dc_auth is None:
        sys.exit(
            "No adapters configured — add at least one of [auth.telegram] or"
            " [auth.discord] to config.toml"
        )

    # Resolve credentials from CredentialStore (bot_id="main" for legacy path).
    # Non-credential config (bot_username, auto_thread) is read from env vars.
    tg_token_lg: str | None = None
    tg_webhook_secret_lg: str | None = None
    tg_bot_username_lg: str = os.environ.get("TELEGRAM_BOT_USERNAME", "lyra_bot")
    if tg_auth is not None:
        tg_creds_lg = await cred_store_legacy.get_full("telegram", "main")
        if tg_creds_lg is None:
            raise MissingCredentialsError("telegram", "main")
        tg_token_lg, tg_webhook_secret_lg = tg_creds_lg

    dc_token_lg: str | None = None
    dc_auto_thread_lg: bool = True
    if dc_auth is not None:
        dc_creds_lg = await cred_store_legacy.get_full("discord", "main")
        if dc_creds_lg is None:
            raise MissingCredentialsError("discord", "main")
        dc_token_lg, _ = dc_creds_lg
        auto_thread_str_lg = os.environ.get("DISCORD_AUTO_THREAD", "").strip().lower()
        dc_auto_thread_lg = (
            auto_thread_str_lg in {"1", "true", "yes", "on"}
            if auto_thread_str_lg
            else True
        )

    agent_config = load_agent_config(
        "lyra_default",
        instance_overrides=_build_agent_overrides(raw_config, "lyra_default"),
    )
    log.info(
        "Agent loaded: name=%s model=%s backend=%s",
        agent_config.name,
        agent_config.model_config.model,
        agent_config.model_config.backend,
    )

    msg_manager = _load_messages(language=agent_config.i18n_language)

    pairing_config = _load_pairing_config(raw_config)
    if pairing_config.enabled and not admin_user_ids:
        log.warning(
            "Pairing is enabled but no admin user_ids configured in "
            "[admin].user_ids — no one can run /invite or /unpair"
        )
    pm_legacy: PairingManager | None = None
    if pairing_config.enabled:
        pm_legacy = PairingManager(
            config=pairing_config,
            db_path=vault_dir_lg / "pairing.db",
            admin_user_ids=admin_user_ids,
            auth_store=auth_store_legacy,
        )
        await pm_legacy.connect()
        set_pairing_manager(pm_legacy)

    stt_service_legacy: STTService | None = None
    if os.environ.get("STT_MODEL_SIZE"):
        try:
            stt_cfg = load_stt_config()
            stt_service_legacy = STTService(stt_cfg)
            log.info("STT enabled: model=%s (via voiceCLI)", stt_cfg.model_size)
        except ValueError as exc:
            raise SystemExit(f"Invalid STT configuration: {exc}") from exc

    tts_service: TTSService | None = None
    tts_cfg = load_tts_config()
    if tts_cfg.engine or tts_cfg.voice or tts_cfg.language:
        tts_service = TTSService(tts_cfg)
        log.info(
            "TTS enabled: engine=%s voice=%s (via voiceCLI)",
            tts_cfg.engine or "default",
            tts_cfg.voice or "default",
        )

    from lyra.core.debouncer import DEFAULT_DEBOUNCE_MS

    hub = Hub(
        circuit_registry=circuit_registry,
        msg_manager=msg_manager,
        pairing_manager=pm_legacy,
        stt=stt_service_legacy,
        debounce_ms=DEFAULT_DEBOUNCE_MS,
    )

    cli_pool_legacy: CliPool | None = None
    if agent_config.model_config.backend == "claude-cli":
        cli_pool_legacy = CliPool()
        await cli_pool_legacy.start()

    sr_config = agent_config.smart_routing
    if (
        sr_config is not None
        and sr_config.enabled
        and agent_config.model_config.backend != "anthropic-sdk"
    ):
        log.warning(
            "smart_routing.enabled=true but backend=%r — "
            "smart routing only applies to anthropic-sdk",
            agent_config.model_config.backend,
        )
    provider_registry, smart_routing_decorator = _build_provider_registry(
        circuit_registry, cli_pool_legacy, smart_routing_config=sr_config
    )

    agent = _create_agent(
        agent_config,
        cli_pool_legacy,
        circuit_registry=circuit_registry,
        admin_user_ids=admin_user_ids,
        msg_manager=msg_manager,
        stt=stt_service_legacy,
        tts=tts_service,
        provider_registry=provider_registry,
        smart_routing_decorator=smart_routing_decorator,
    )
    hub.register_agent(agent)

    tg_adapter: TelegramAdapter | None = None
    dc_adapter: DiscordAdapter | None = None

    if tg_auth is not None and tg_token_lg is not None:
        tg_adapter = TelegramAdapter(
            bot_id="main",
            token=tg_token_lg,
            hub=hub,
            bot_username=tg_bot_username_lg,
            webhook_secret=tg_webhook_secret_lg or "",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auth=tg_auth,
        )
        hub.register_adapter(Platform.TELEGRAM, "main", tg_adapter)
        tg_key = RoutingKey(Platform.TELEGRAM, "main", "*")
        hub.register_binding(
            Platform.TELEGRAM, "main", "*", agent.name, tg_key.to_pool_id()
        )

    if dc_auth is not None and dc_token_lg is not None:
        dc_adapter = DiscordAdapter(
            hub=hub,
            bot_id="main",
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            auto_thread=dc_auto_thread_lg,
            auth=dc_auth,
        )
        hub.register_adapter(Platform.DISCORD, "main", dc_adapter)
        dc_key = RoutingKey(Platform.DISCORD, "main", "*")
        hub.register_binding(
            Platform.DISCORD, "main", "*", agent.name, dc_key.to_pool_id()
        )

    # Create and register per-platform outbound dispatchers
    tg_dispatcher_legacy: OutboundDispatcher | None = None
    dc_dispatcher_legacy: OutboundDispatcher | None = None

    if tg_adapter is not None:
        tg_dispatcher_legacy = OutboundDispatcher(
            platform_name="telegram",
            adapter=tg_adapter,
            circuit=circuit_registry.get("telegram"),
            circuit_registry=circuit_registry,
            bot_id="main",
        )
        hub.register_outbound_dispatcher(
            Platform.TELEGRAM, "main", tg_dispatcher_legacy
        )

    if dc_adapter is not None:
        dc_dispatcher_legacy = OutboundDispatcher(
            platform_name="discord",
            adapter=dc_adapter,
            circuit=circuit_registry.get("discord"),
            circuit_registry=circuit_registry,
            bot_id="main",
        )
        hub.register_outbound_dispatcher(Platform.DISCORD, "main", dc_dispatcher_legacy)

    # Start InboundBus feeder tasks and OutboundDispatcher workers
    await hub.inbound_bus.start()
    await hub.inbound_audio_bus.start()
    if tg_dispatcher_legacy is not None:
        await tg_dispatcher_legacy.start()
    if dc_dispatcher_legacy is not None:
        await dc_dispatcher_legacy.start()

    # Health endpoint (SC-1): root FastAPI app on configurable port
    health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
    health_app = create_health_app(hub)
    health_config = uvicorn.Config(
        health_app, host="127.0.0.1", port=health_port, log_level="warning"
    )
    health_server = uvicorn.Server(health_config)

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        _loop = asyncio.get_running_loop()
        _loop.add_signal_handler(signal.SIGINT, stop.set)
        _loop.add_signal_handler(signal.SIGTERM, stop.set)

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(hub._audio_loop(), name="hub-audio"),
        asyncio.create_task(health_server.serve(), name="health"),
    ]
    if tg_adapter is not None:
        tasks.append(
            asyncio.create_task(
                # handle_signals=False is mandatory when aiogram runs inside
                # asyncio.gather() — otherwise it installs its own signal handlers
                # that conflict with the event loop's, causing double-cancellation.
                tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
                name="telegram",
            )
        )
    if dc_adapter is not None and dc_token_lg is not None:
        tasks.append(asyncio.create_task(dc_adapter.start(dc_token_lg), name="discord"))

    task_names = {t.get_name() for t in tasks}
    active = [name for name in ("telegram", "discord") if name in task_names]
    log.info(
        "Lyra started — adapters: %s, health on :%d.",
        ", ".join(active) if active else "none",
        health_port,
    )

    await stop.wait()

    log.info("Shutdown signal received — stopping…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await hub.inbound_bus.stop()
    await hub.inbound_audio_bus.stop()
    if tg_dispatcher_legacy is not None:
        await tg_dispatcher_legacy.stop()
    if dc_dispatcher_legacy is not None:
        await dc_dispatcher_legacy.stop()
    if dc_adapter is not None:
        await dc_adapter.close()
    if pm_legacy is not None:
        await pm_legacy.close()
    await cred_store_legacy.close()
    await auth_store_legacy.close()
    if cli_pool_legacy is not None:
        await cli_pool_legacy.stop()
    log.info("Lyra stopped.")


def _parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse daemon startup arguments."""
    parser = argparse.ArgumentParser(
        description="Lyra daemon — start Telegram/Discord adapters",
        add_help=True,
    )
    parser.add_argument(
        "--adapter",
        choices=["telegram", "discord", "all"],
        default="all",
        help=(
            "Which adapter(s) to start. "
            "'telegram' starts only Telegram adapters, "
            "'discord' starts only Discord adapters, "
            "'all' starts both (default)."
        ),
    )
    return parser.parse_args(args)


async def _main(*, adapter: str = "all", _stop: asyncio.Event | None = None) -> None:
    """Wire hub + adapters and run until stop event fires.

    The optional _stop parameter is for testing: pass a pre-set Event to exit
    immediately after setup without registering signal handlers.

    Supports two configuration schemas:
      - New multi-bot: [[telegram.bots]] / [[discord.bots]] arrays in config.toml.
      - Legacy single-bot: [auth.telegram] / [auth.discord] flat sections
        (backward compat).

    When the new schema is detected (at least one bot list is non-empty), each bot is
    wired individually with its own AuthMiddleware, OutboundDispatcher, and adapter.
    When only the legacy schema is found, behavior is identical to the original
    single-bot implementation.
    """
    load_dotenv()
    if not os.environ.get("LYRA_HEALTH_SECRET"):
        log.warning(
            "LYRA_HEALTH_SECRET is not set — /health returns minimal response only"
        )
    raw_config = _load_raw_config()
    circuit_registry, admin_user_ids = _load_circuit_config(raw_config)
    set_admin_user_ids(admin_user_ids)
    try:
        tg_multi_cfg, dc_multi_cfg = load_multibot_config(raw_config)
    except ValueError as exc:
        sys.exit(str(exc))

    # Apply adapter filter BEFORE bootstrap — guard evaluates post-filter state
    original_tg_count = len(tg_multi_cfg.bots)
    original_dc_count = len(dc_multi_cfg.bots)
    if adapter == "telegram":
        dc_multi_cfg = DiscordMultiConfig(bots=[])
    elif adapter == "discord":
        tg_multi_cfg = TelegramMultiConfig(bots=[])

    use_multibot = bool(tg_multi_cfg.bots or dc_multi_cfg.bots)

    # Guard: adapter flag specified but no matching bots in config
    if not use_multibot and adapter != "all":
        requested_count = (
            original_tg_count if adapter == "telegram" else original_dc_count
        )
        if requested_count == 0:
            sys.exit(
                f"--adapter {adapter} specified but no [[{adapter}.bots]] entries found"
                " in config.toml — add at least one bot under [[{adapter}.bots]]"
                " or omit --adapter to start all configured adapters"
            )
    try:
        if use_multibot:
            await _bootstrap_multibot(
                raw_config,
                circuit_registry,
                admin_user_ids,
                tg_multi_cfg,
                dc_multi_cfg,
                _stop=_stop,
            )
        else:
            await _bootstrap_legacy(
                raw_config,
                circuit_registry,
                admin_user_ids,
                adapter=adapter,
                _stop=_stop,
            )
    except (MissingCredentialsError, KeyringError) as exc:
        sys.exit(str(exc))


def _setup_logging() -> None:
    """Configure logging: console + rotating file in ~/.lyra/logs/."""
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    log_dir = Path.home() / ".lyra" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{stamp}_lyra.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,  # 10 MB per file, 5 backups
    )
    file_handler.setFormatter(logging.Formatter(fmt))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])

    logging.getLogger(__name__).info("Logging to %s", log_file)


def main() -> None:
    _setup_logging()
    parsed = _parse_args()
    asyncio.run(_main(adapter=parsed.adapter))


if __name__ == "__main__":
    main()
