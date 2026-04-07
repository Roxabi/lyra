"""Bootstrap standalone Hub — NATS-connected Hub without embedded adapters."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.agent_factory import _resolve_agents, _resolve_bot_agent_map
from lyra.bootstrap.bootstrap_stores import open_stores
from lyra.bootstrap.bootstrap_wiring import _build_bot_auths
from lyra.bootstrap.config import (
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
from lyra.bootstrap.health import create_health_app
from lyra.bootstrap.lifecycle_helpers import (
    setup_signal_handlers,
    teardown_buses,
    teardown_dispatchers,
)
from lyra.bootstrap.voice_overlay import init_nats_stt, init_nats_tts
from lyra.config import load_multibot_config
from lyra.core.agent import Agent
from lyra.core.agent_loader import agent_row_to_config
from lyra.core.cli_pool import CliPool
from lyra.core.hub import Hub
from lyra.core.hub.event_bus import PipelineEventBus
from lyra.core.hub.outbound_dispatcher import OutboundDispatcher
from lyra.core.message import InboundMessage, Platform
from lyra.core.stores.pairing import PairingManager, set_pairing_manager
from lyra.nats import nats_connect
from lyra.nats.connect import scrub_nats_url
from lyra.nats.nats_bus import NatsBus
from lyra.nats.nats_channel_proxy import NatsChannelProxy
from lyra.nats.queue_groups import HUB_INBOUND
from lyra.nats.readiness import start_readiness_responder

log = logging.getLogger(__name__)

_LOCKFILE = Path.home() / ".lyra" / "hub.lock"


def _release_lockfile() -> None:
    """Remove the Hub lockfile if it exists."""
    try:
        _LOCKFILE.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Could not remove lockfile %s: %s", _LOCKFILE, exc)


def _acquire_lockfile() -> None:
    """Write current PID to the lockfile.

    If the lockfile already exists and the recorded PID is still alive,
    log an error and exit — another Hub process is running.
    Registers an atexit handler to clean up the lockfile on normal exit.
    """
    if _LOCKFILE.exists():
        try:
            pid_str = _LOCKFILE.read_text().strip()
            pid = int(pid_str)
            try:
                os.kill(pid, 0)  # signal 0 = existence check only
                sys.exit(
                    f"Hub is already running (PID {pid}). "
                    f"Remove {_LOCKFILE} if the process is stale."
                )
            except ProcessLookupError:
                # PID no longer alive — stale lockfile, safe to overwrite
                log.warning(
                    "Stale lockfile found (PID %d not running) — overwriting", pid
                )
            except PermissionError:
                # PID exists but we can't signal it — treat as alive
                sys.exit(
                    f"Hub is already running (PID {pid}, permission denied). "
                    f"Remove {_LOCKFILE} if the process is stale."
                )
        except (ValueError, OSError) as exc:
            log.warning("Could not read lockfile %s (%s) — overwriting", _LOCKFILE, exc)

    _LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCKFILE.write_text(str(os.getpid()))
    atexit.register(_release_lockfile)


async def _notify_startup(
    active_proxies: list[str], health_port: int
) -> None:
    """Send a Telegram notification on hub startup (best-effort)."""
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
    if not token or not chat_id:
        log.debug("Startup notification skipped — TELEGRAM_TOKEN or CHAT_ID not set")
        return

    proxies = ", ".join(active_proxies) if active_proxies else "none"
    text = f"✅ Lyra Hub started\n\nProxies: {proxies}\nHealth: :{health_port}"

    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
        if resp.status_code != 200:
            log.warning("Startup notification failed: HTTP %d", resp.status_code)
        else:
            log.info("Startup notification sent to Telegram")
    except Exception as exc:
        log.warning("Startup notification failed: %s", exc)


async def _bootstrap_hub_standalone(  # noqa: C901, PLR0915 — startup wiring
    raw_config: dict,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire standalone Hub connected to NATS and run until stop.

    The Hub receives inbound messages via NATS subscriptions (NatsBus) and
    dispatches outbound responses via NatsChannelProxy — no platform SDKs are
    embedded in this process.

    Requires:
        NATS_URL: NATS server URL (e.g. ``nats://localhost:4222``).
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit(
            "NATS_URL is required for standalone Hub mode. "
            "Set NATS_URL=nats://localhost:4222 (or your NATS server address)."
        )

    _acquire_lockfile()

    try:
        nc = await nats_connect(nats_url)
        log.info("Connected to NATS at %s", scrub_nats_url(nats_url))
    except Exception as exc:
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

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
        # Prune stale message_index entries
        mi_cfg = MessageIndexConfig(**raw_config.get("message_index", {}))
        pruned = await stores.message_index.cleanup_older_than(mi_cfg.retention_days)
        if pruned:
            log.info(
                "message_index: pruned %d entries older than %d days",
                pruned,
                mi_cfg.retention_days,
            )

        # Seed per-bot owner/trusted users as permanent grants
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
                overrides = _build_agent_overrides(raw_config, n)
                agent_configs[n] = agent_row_to_config(
                    row, instance_overrides=overrides.model_dump()
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

        # STT / TTS via NATS clients (hub talks to voicecli adapters over NATS)
        stt_service = init_nats_stt(nc)
        tts_service = init_nats_tts(nc)

        cli_pool_cfg = _load_cli_pool_config(raw_config)
        hub_cfg = _load_hub_config(raw_config)
        pool_cfg = _load_pool_config(raw_config)
        llm_cfg = _load_llm_config(raw_config)
        debouncer_cfg = _load_debouncer_config(raw_config)
        event_bus_cfg = _load_event_bus_config(raw_config)
        event_bus = PipelineEventBus(maxsize=event_bus_cfg.queue_maxsize)

        hub = Hub(
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            pairing_manager=pm,
            stt=stt_service,
            tts=tts_service,
            debounce_ms=debouncer_cfg.default_debounce_ms,
            cancel_on_new_message=debouncer_cfg.cancel_on_new_message,
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
            event_bus=event_bus,
            inbound_bus=inbound_bus,
        )
        hub.set_turn_store(stores.turn)
        hub.set_message_index(stores.message_index)

        # Build cli_pool if any agent needs it
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

        # Wire each (platform, bot_id) to a NatsChannelProxy + OutboundDispatcher
        from lyra.core.hub.hub_protocol import RoutingKey

        dispatchers: list[OutboundDispatcher] = []
        proxies: list[NatsChannelProxy] = []

        for bot_cfg, auth in tg_bot_auths:
            resolved_agent = bot_agent_map.get(("telegram", bot_cfg.bot_id))
            if resolved_agent is None:
                log.warning(
                    "telegram bot_id=%r not in bot_agent_map — skipping",
                    bot_cfg.bot_id,
                )
                continue

            proxy = NatsChannelProxy(
                nc=nc, platform=Platform.TELEGRAM, bot_id=bot_cfg.bot_id
            )
            proxies.append(proxy)
            hub.register_authenticator(Platform.TELEGRAM, bot_cfg.bot_id, auth)
            hub.register_adapter(Platform.TELEGRAM, bot_cfg.bot_id, proxy)

            tg_key = RoutingKey(Platform.TELEGRAM, bot_cfg.bot_id, "*")
            hub.register_binding(
                Platform.TELEGRAM,
                bot_cfg.bot_id,
                "*",
                resolved_agent,
                tg_key.to_pool_id(),
            )

            dispatcher = OutboundDispatcher(
                platform_name="telegram",
                adapter=proxy,
                circuit=circuit_registry.get("telegram"),
                circuit_registry=circuit_registry,
                bot_id=bot_cfg.bot_id,
            )
            hub.register_outbound_dispatcher(
                Platform.TELEGRAM, bot_cfg.bot_id, dispatcher
            )
            dispatchers.append(dispatcher)
            log.info(
                "Registered NATS proxy: telegram bot_id=%r agent=%r",
                bot_cfg.bot_id,
                resolved_agent,
            )

        for bot_cfg, auth in dc_bot_auths:
            resolved_agent = bot_agent_map.get(("discord", bot_cfg.bot_id))
            if resolved_agent is None:
                log.warning(
                    "discord bot_id=%r not in bot_agent_map — skipping",
                    bot_cfg.bot_id,
                )
                continue

            proxy = NatsChannelProxy(
                nc=nc, platform=Platform.DISCORD, bot_id=bot_cfg.bot_id
            )
            proxies.append(proxy)
            hub.register_authenticator(Platform.DISCORD, bot_cfg.bot_id, auth)
            hub.register_adapter(Platform.DISCORD, bot_cfg.bot_id, proxy)

            dc_key = RoutingKey(Platform.DISCORD, bot_cfg.bot_id, "*")
            hub.register_binding(
                Platform.DISCORD,
                bot_cfg.bot_id,
                "*",
                resolved_agent,
                dc_key.to_pool_id(),
            )

            dispatcher = OutboundDispatcher(
                platform_name="discord",
                adapter=proxy,
                circuit=circuit_registry.get("discord"),
                circuit_registry=circuit_registry,
                bot_id=bot_cfg.bot_id,
            )
            hub.register_outbound_dispatcher(
                Platform.DISCORD, bot_cfg.bot_id, dispatcher
            )
            dispatchers.append(dispatcher)
            log.info(
                "Registered NATS proxy: discord bot_id=%r agent=%r",
                bot_cfg.bot_id,
                resolved_agent,
            )

        # Lifecycle: start buses, dispatchers, hub, health server
        await hub.inbound_bus.start()
        for d in dispatchers:
            await d.start()

        readiness_sub = await start_readiness_responder(
            nc, [hub.inbound_bus]
        )

        import uvicorn

        health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
        health_app = create_health_app(hub)
        health_config = uvicorn.Config(
            health_app, host="127.0.0.1", port=health_port, log_level="warning"
        )
        health_server = uvicorn.Server(health_config)

        stop = _stop if _stop is not None else asyncio.Event()
        if _stop is None:
            setup_signal_handlers(stop)

        from lyra.bootstrap.utils import watchdog

        tasks = [
            asyncio.create_task(hub.run(), name="hub"),
            asyncio.create_task(health_server.serve(), name="health"),
        ]

        if hub._event_bus is not None:
            from lyra.core.hub.audit_consumer import AuditConsumer

            _audit_queue = hub._event_bus.subscribe()
            _audit_consumer = AuditConsumer(_audit_queue)
            tasks.append(
                asyncio.create_task(_audit_consumer.run(), name="audit-consumer")
            )

        active = (
            [f"telegram:{c.bot_id}" for c, _ in tg_bot_auths]
            + [f"discord:{c.bot_id}" for c, _ in dc_bot_auths]
        )
        log.info(
            "Hub standalone started — NATS proxies: %s, health on :%d.",
            ", ".join(active) if active else "none",
            health_port,
        )

        await _notify_startup(active, health_port)

        await watchdog(tasks, stop)

        log.info("Shutdown signal received — stopping...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await readiness_sub.unsubscribe()
        await teardown_buses(hub.inbound_bus)
        await teardown_dispatchers(dispatchers)
        for proxy in proxies:
            await proxy.publish_stream_errors("hub_shutdown")
        if pm is not None:
            await pm.close()
        if cli_pool is not None:
            await cli_pool.drain(timeout=60.0)
            active_ids = cli_pool.get_active_pool_ids()
            if active_ids:
                await hub.notify_shutdown_inflight(active_ids)
            await cli_pool.stop()
        await hub.shutdown()

    # Close NATS connection after stores context exits
    try:
        await nc.close()
        log.info("NATS connection closed.")
    except Exception as exc:
        log.warning("Error closing NATS connection: %s", exc)

    _release_lockfile()
    log.info("Hub standalone stopped.")
