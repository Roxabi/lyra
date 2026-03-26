"""Bootstrap multibot — hub wiring for multi-bot configuration schema."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.agent_factory import (
    _resolve_agents,
    _resolve_bot_agent_map,
)
from lyra.bootstrap.config import (
    _build_agent_overrides,
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_messages,
    _load_pairing_config,
    _load_pool_config,
)
from lyra.bootstrap.multibot_stores import open_stores
from lyra.bootstrap.multibot_wiring import (
    run_lifecycle,
    wire_discord_adapters,
    wire_telegram_adapters,
)
from lyra.bootstrap.voice_overlay import init_stt, init_tts
from lyra.config import (
    DiscordBotConfig,
    DiscordMultiConfig,
    TelegramBotConfig,
    TelegramMultiConfig,
)
from lyra.core.agent import Agent
from lyra.core.agent_loader import agent_row_to_config
from lyra.core.auth import AuthMiddleware
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool
from lyra.core.hub import Hub
from lyra.core.stores.auth_store import AuthStore
from lyra.core.stores.pairing import PairingManager, set_pairing_manager

log = logging.getLogger(__name__)


async def _bootstrap_multibot(  # noqa: C901, PLR0915 — startup wiring
    raw_config: dict,
    circuit_registry: CircuitRegistry,
    admin_user_ids: frozenset[str],
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
    vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with open_stores(vault_dir) as stores:
        # Seed per-bot owner/trusted users as permanent grants
        auth_block: dict = raw_config.get("auth", {})
        for entry in auth_block.get("telegram_bots", []):
            synthetic = {"auth": {"telegram": entry}}
            await stores.auth.seed_from_config(synthetic, "telegram")
        for entry in auth_block.get("discord_bots", []):
            synthetic = {"auth": {"discord": entry}}
            await stores.auth.seed_from_config(synthetic, "discord")

        tg_bot_auths, dc_bot_auths = _build_bot_auths(
            raw_config,
            tg_multi_cfg,
            dc_multi_cfg,
            stores.auth,
            admin_user_ids,
        )
        log.info("Authenticator: %d admin_user_id(s) configured", len(admin_user_ids))

        if not tg_bot_auths and not dc_bot_auths:
            sys.exit(
                "No adapters configured — add at least one [[telegram.bots]] or"
                " [[discord.bots]] entry with a matching [[auth.telegram_bots]] or"
                " [[auth.discord_bots]] section to config.toml"
            )

        # Resolve (platform, bot_id) -> agent_name
        bot_agent_map = await _resolve_bot_agent_map(
            stores.agent, tg_multi_cfg.bots, dc_multi_cfg.bots
        )
        agent_names: set[str] = set(bot_agent_map.values())

        # Load all agent configs from DB
        agent_configs: dict[str, Agent] = {}
        for n in sorted(agent_names):
            row = stores.agent.get(n)
            if row is not None:
                agent_configs[n] = agent_row_to_config(
                    row,
                    instance_overrides=_build_agent_overrides(raw_config, n).model_dump(),
                )
            else:
                log.error("Agent %r not found in DB — skipping", n)
        if not agent_configs:
            sys.exit(
                "No agent configs could be loaded — run 'lyra agent init' to seed the"
                " agents table"
            )
        first_agent_name = next(iter(sorted(agent_configs)))
        first_agent_config = agent_configs[first_agent_name]

        msg_manager = _load_messages(language=first_agent_config.i18n_language)

        # Pairing manager
        pairing_config = _load_pairing_config(raw_config)
        if pairing_config.enabled and not admin_user_ids:
            log.warning(
                "Pairing enabled but [admin].user_ids is empty — "
                "/invite and /unpair require is_admin=True "
                "(granted to [admin].user_ids entries "
                "or users configured as OWNER in [[auth.*_bots]])"
            )
        pm: PairingManager | None = None
        if pairing_config.enabled:
            pm = PairingManager(
                config=pairing_config,
                db_path=vault_dir / "pairing.db",
                auth_store=stores.auth,
            )
            await pm.connect()
            set_pairing_manager(pm)

        # STT / TTS services
        stt_service = init_stt(first_agent_config)
        tts_service = init_tts(stt_service)

        cli_pool_cfg = _load_cli_pool_config(raw_config)
        hub_cfg = _load_hub_config(raw_config)
        pool_cfg = _load_pool_config(raw_config)
        llm_cfg = _load_llm_config(raw_config)
        inbound_bus_cfg = _load_inbound_bus_config(raw_config)
        debouncer_cfg = _load_debouncer_config(raw_config)

        hub = Hub(
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            pairing_manager=pm,
            stt=stt_service,
            tts=tts_service,
            debounce_ms=debouncer_cfg.default_debounce_ms,
            prefs_store=stores.prefs,
            turn_timeout=cli_pool_cfg.turn_timeout,
            pool_ttl=hub_cfg.pool_ttl,
            rate_limit=hub_cfg.rate_limit,
            rate_window=hub_cfg.rate_window,
            max_sdk_history=pool_cfg.max_sdk_history,
            safe_dispatch_timeout=pool_cfg.safe_dispatch_timeout,
            staging_maxsize=inbound_bus_cfg.staging_maxsize,
            platform_queue_maxsize=inbound_bus_cfg.platform_queue_maxsize,
            queue_depth_threshold=inbound_bus_cfg.queue_depth_threshold,
            max_merged_chars=debouncer_cfg.max_merged_chars,
        )
        hub.set_turn_store(stores.turn)
        hub.set_message_index(stores.message_index)

        # Build cli_pool if any agent needs it
        cli_pool: CliPool | None = None
        for cfg in agent_configs.values():
            if cfg.model_config.backend == "claude-cli":
                cli_pool = CliPool(
                    idle_ttl=cli_pool_cfg.idle_ttl,
                    default_timeout=cli_pool_cfg.default_timeout,
                    reaper_interval=cli_pool_cfg.reaper_interval,
                    kill_timeout=cli_pool_cfg.kill_timeout,
                    read_buffer_bytes=cli_pool_cfg.read_buffer_bytes,
                    stdin_drain_timeout=cli_pool_cfg.stdin_drain_timeout,
                    max_idle_retries=cli_pool_cfg.max_idle_retries,
                    intermediate_timeout=cli_pool_cfg.intermediate_timeout,
                )
                await cli_pool.start()
                break
        hub.cli_pool = cli_pool

        # Create and register all agents
        all_agents = _resolve_agents(
            agent_configs,
            cli_pool,
            circuit_registry,
            msg_manager,
            stt_service,
            tts_service,
            agent_store=stores.agent,
            llm_cfg=llm_cfg,
        )
        for ag in all_agents.values():
            hub.register_agent(ag)

        # Wire adapters via helpers
        tg_adapters, tg_dispatchers = await wire_telegram_adapters(
            hub,
            tg_bot_auths,
            bot_agent_map,
            stores.cred,
            circuit_registry,
            msg_manager,
        )
        dc_adapters, dc_dispatchers = await wire_discord_adapters(
            hub,
            dc_bot_auths,
            bot_agent_map,
            stores.cred,
            circuit_registry,
            msg_manager,
            stores.thread,
            agent_store=stores.agent,
        )

        # Start buses, dispatchers, health server, adapter tasks
        await run_lifecycle(
            hub,
            tg_adapters,
            tg_dispatchers,
            dc_adapters,
            dc_dispatchers,
            pm,
            cli_pool,
            _stop,
        )


def _build_bot_auths(
    raw_config: dict,
    tg_multi_cfg: TelegramMultiConfig,
    dc_multi_cfg: DiscordMultiConfig,
    auth_store: AuthStore,
    admin_user_ids: frozenset[str] = frozenset(),
) -> tuple[
    list[tuple[TelegramBotConfig, AuthMiddleware]],
    list[tuple[DiscordBotConfig, AuthMiddleware]],
]:
    """Build (bot_cfg, auth) pairs for each platform, skipping bots without auth."""
    tg_bot_auths: list[tuple[TelegramBotConfig, AuthMiddleware]] = []
    dc_bot_auths: list[tuple[DiscordBotConfig, AuthMiddleware]] = []

    try:
        for bot_cfg in tg_multi_cfg.bots:
            auth = AuthMiddleware.from_bot_config(
                raw_config,
                "telegram",
                bot_cfg.bot_id,
                store=auth_store,
                admin_user_ids=admin_user_ids,
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
                raw_config,
                "discord",
                bot_cfg.bot_id,
                store=auth_store,
                admin_user_ids=admin_user_ids,
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

    return tg_bot_auths, dc_bot_auths
