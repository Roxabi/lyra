"""Unified bootstrap — single-process hub + adapters with optional embedded NATS."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.bootstrap_stores import open_stores
from lyra.bootstrap.factory.agent_factory import _resolve_agents, _resolve_bot_agent_map
from lyra.bootstrap.factory.config import (
    MessageIndexConfig,
    _build_agent_overrides,
    _load_circuit_config,
    _load_cli_pool_config,
    _load_debouncer_config,
    _load_event_bus_config,
    _load_hub_config,
    _load_inbound_bus_config,
    _load_llm_config,
    _load_messages,
    _load_pairing_config,
    _load_pool_config,
)
from lyra.bootstrap.infra.embedded_nats import ensure_nats
from lyra.bootstrap.infra.lockfile import acquire_lockfile, release_lockfile
from lyra.bootstrap.lifecycle.bootstrap_lifecycle import run_lifecycle
from lyra.bootstrap.wiring.bootstrap_wiring import (
    _build_bot_auths,
    wire_discord_adapters,
    wire_telegram_adapters,
)
from lyra.config import load_multibot_config
from lyra.core.agent import Agent
from lyra.core.agent.agent_loader import agent_row_to_config
from lyra.core.cli.cli_pool import CliPool
from lyra.core.config import HubConfig
from lyra.core.hub import Hub
from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.messaging.message import InboundMessage
from lyra.infrastructure.stores.pairing import PairingManager, set_pairing_manager
from lyra.nats.nats_bus import NatsBus
from lyra.nats.queue_groups import HUB_INBOUND

log = logging.getLogger(__name__)


async def _bootstrap_unified(  # noqa: C901, PLR0915
    raw_config: dict,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire hub + adapters in one process with NATS (embedded or external)."""
    nc, embedded, _ = await ensure_nats(os.environ.get("NATS_URL"))
    acquire_lockfile()
    nats_llm_driver = None
    try:
        inbound_bus_cfg = _load_inbound_bus_config(raw_config)
        inbound_bus: NatsBus[InboundMessage] = NatsBus(
            nc=nc,
            bot_id="hub",
            item_type=InboundMessage,
            staging_maxsize=inbound_bus_cfg.staging_maxsize,
            queue_group=HUB_INBOUND,
        )
        vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
        vault_dir.mkdir(parents=True, exist_ok=True)

        async with open_stores(vault_dir) as stores:
            mi_cfg = MessageIndexConfig(**raw_config.get("message_index", {}))
            pruned = await stores.message_index.cleanup_older_than(
                mi_cfg.retention_days
            )
            if pruned:
                log.info(
                    "message_index: pruned %d entries older than %d days",
                    pruned,
                    mi_cfg.retention_days,
                )

            auth_block: dict = raw_config.get("auth", {})
            for entry in auth_block.get("telegram_bots", []):
                synthetic = {"auth": {"telegram": entry}}
                await stores.auth.seed_from_config(synthetic, "telegram")
            for entry in auth_block.get("discord_bots", []):
                synthetic = {"auth": {"discord": entry}}
                await stores.auth.seed_from_config(synthetic, "discord")

            circuit_registry, admin_user_ids = _load_circuit_config(raw_config)

            try:
                tg_multi_cfg, dc_multi_cfg = load_multibot_config(raw_config)
            except ValueError as exc:
                sys.exit(str(exc))

            tg_bot_auths, dc_bot_auths = _build_bot_auths(
                raw_config,
                tg_multi_cfg,
                dc_multi_cfg,
                stores.auth,
                admin_user_ids,
                alias_store=stores.identity_alias,
            )
            log.info(
                "Authenticator: %d admin_user_id(s) configured",
                len(admin_user_ids),
            )

            if not tg_bot_auths and not dc_bot_auths:
                sys.exit(
                    "No adapters configured — add at least one"
                    " [[telegram.bots]] or [[discord.bots]] entry with"
                    " a matching [[auth.telegram_bots]] or"
                    " [[auth.discord_bots]] section to config.toml"
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
                sys.exit(
                    "No agent configs could be loaded — run"
                    " 'lyra agent init' to seed the agents table"
                )
            first_agent_name = next(iter(sorted(agent_configs)))
            first_agent_config = agent_configs[first_agent_name]

            msg_manager = _load_messages(language=first_agent_config.i18n_language)

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

            from lyra.bootstrap.factory.llm_overlay import init_nats_llm
            from lyra.bootstrap.factory.voice_overlay import (
                init_nats_stt,
                init_nats_tts,
            )

            stt_service = init_nats_stt(nc)
            await stt_service.start()
            tts_service = init_nats_tts(nc)
            await tts_service.start()
            nats_llm_driver = await init_nats_llm(nc)
            cli_pool_cfg = _load_cli_pool_config(raw_config)
            hub_cfg = _load_hub_config(raw_config)
            pool_cfg = _load_pool_config(raw_config)
            llm_cfg = _load_llm_config(raw_config)
            debouncer_cfg = _load_debouncer_config(raw_config)
            event_bus_cfg = _load_event_bus_config(raw_config)
            event_bus = PipelineEventBus(maxsize=event_bus_cfg.queue_maxsize)

            hub_config = HubConfig(
                rate_limit=hub_cfg.rate_limit,
                rate_window=hub_cfg.rate_window,
                pool_ttl=hub_cfg.pool_ttl,
                debounce_ms=debouncer_cfg.default_debounce_ms,
                cancel_on_new_message=debouncer_cfg.cancel_on_new_message,
                turn_timeout=cli_pool_cfg.turn_timeout,
                safe_dispatch_timeout=pool_cfg.safe_dispatch_timeout,
                staging_maxsize=inbound_bus_cfg.staging_maxsize,
                platform_queue_maxsize=inbound_bus_cfg.platform_queue_maxsize,
                queue_depth_threshold=inbound_bus_cfg.queue_depth_threshold,
                max_merged_chars=debouncer_cfg.max_merged_chars,
            )

            hub = Hub(
                circuit_registry=circuit_registry,
                msg_manager=msg_manager,
                pairing_manager=pm,
                stt=stt_service,
                tts=tts_service,
                prefs_store=stores.prefs,
                event_bus=event_bus,
                inbound_bus=inbound_bus,
                config=hub_config,
            )
            hub.set_turn_store(stores.turn)
            hub.set_message_index(stores.message_index)

            # Wire alias_store (#472)
            stores.prefs.set_alias_store(stores.identity_alias)
            hub.set_alias_store(stores.identity_alias)

            cli_pool: CliPool | None = None
            for cfg in agent_configs.values():
                if cfg.llm_config.backend == "claude-cli":
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
                    cli_pool.set_turn_store(stores.turn)
                    break
            hub.cli_pool = cli_pool

            all_agents = _resolve_agents(
                agent_configs,
                cli_pool,
                circuit_registry,
                msg_manager,
                stt_service,
                tts_service,
                agent_store=stores.agent,
                llm_cfg=llm_cfg,
                nats_llm_driver=nats_llm_driver,
            )
            for ag in all_agents.values():
                hub.register_agent(ag)

            # Wire alias_store into MemoryManagers (#472)
            for agent in hub.agent_registry.values():
                mem = getattr(agent, "_memory", None)
                if mem is not None and hasattr(mem, "set_alias_store"):
                    mem.set_alias_store(stores.identity_alias)

            # Wire adapters
            tg_adapters, tg_dispatchers = await wire_telegram_adapters(
                hub,
                tg_bot_auths,
                bot_agent_map,
                stores.cred,
                circuit_registry,
                msg_manager,
                nats_client=nc,
            )
            dc_adapters, dc_dispatchers = await wire_discord_adapters(
                hub,
                dc_bot_auths,
                bot_agent_map,
                stores.cred,
                circuit_registry,
                msg_manager,
                agent_store=stores.agent,
                vault_dir=str(vault_dir),
                nats_client=nc,
            )

            await run_lifecycle(
                hub,
                tg_adapters,
                tg_dispatchers,
                dc_adapters,
                dc_dispatchers,
                pm,
                cli_pool,
                _stop,
                nc=nc,
            )

    finally:
        if nats_llm_driver is not None:
            await nats_llm_driver.stop()
        try:
            await nc.close()
            log.info("NATS connection closed.")
        except Exception as exc:
            log.warning("Error closing NATS connection: %s", exc)
        if embedded:
            await embedded.stop()
        release_lockfile()

    log.info("Lyra stopped.")
