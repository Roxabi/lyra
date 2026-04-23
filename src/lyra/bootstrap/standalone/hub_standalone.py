"""Bootstrap standalone Hub — NATS-connected Hub without embedded adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.auth_seeding import build_bot_auths, seed_auth_store
from lyra.bootstrap.bootstrap_stores import open_stores
from lyra.bootstrap.factory.agent_factory import _resolve_bot_agent_map
from lyra.bootstrap.factory.config import MessageIndexConfig
from lyra.bootstrap.factory.hub_builder import (
    build_cli_pool,
    build_hub,
    build_inbound_bus,
    register_agents,
)
from lyra.bootstrap.factory.llm_overlay import init_nats_llm
from lyra.bootstrap.factory.voice_overlay import init_nats_stt, init_nats_tts
from lyra.bootstrap.infra.health import create_health_app
from lyra.bootstrap.infra.lockfile import acquire_lockfile, release_lockfile
from lyra.bootstrap.infra.notify import notify_startup
from lyra.bootstrap.lifecycle.lifecycle_helpers import setup_signal_handlers
from lyra.bootstrap.standalone.hub_standalone_helpers import (
    build_pairing_manager,
    load_agent_configs,
    shutdown_hub_runtime,
)
from lyra.bootstrap.wiring.nats_wiring import (
    wire_nats_discord_proxies,
    wire_nats_telegram_proxies,
)
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url
from roxabi_nats.readiness import start_readiness_responder

log = logging.getLogger(__name__)


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

    acquire_lockfile()

    try:
        nc = await nats_connect(nats_url, inbox_prefix="_INBOX.hub")
        log.info("Connected to NATS at %s", scrub_nats_url(nats_url))
    except Exception as exc:
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    inbound_bus, inbound_bus_cfg = build_inbound_bus(nc, raw_config)

    vault_dir = Path(
        os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))
    ).resolve()
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

        await seed_auth_store(stores.auth, raw_config)

        try:
            circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths = (
                build_bot_auths(raw_config, stores.auth)
            )
        except ValueError as exc:
            log.error("Configuration error: %s", exc)
            sys.exit(str(exc))

        # Resolve (platform, bot_id) -> agent_name
        bot_agent_map = await _resolve_bot_agent_map(
            stores.agent,
            [cfg for cfg, _ in tg_bot_auths],
            [cfg for cfg, _ in dc_bot_auths],
        )

        agent_configs = load_agent_configs(
            stores.agent, raw_config, set(bot_agent_map.values())
        )
        if not agent_configs:
            sys.exit(
                "No agent configs could be loaded — run 'lyra agent init' to seed the"
                " agents table"
            )
        first_agent_config = agent_configs[next(iter(sorted(agent_configs)))]

        from lyra.bootstrap.factory.config import _load_messages

        msg_manager = _load_messages(language=first_agent_config.i18n_language)

        pm = await build_pairing_manager(
            raw_config,
            vault_dir=vault_dir,
            auth_store=stores.auth,
            admin_user_ids=admin_user_ids,
        )

        # STT / TTS via NATS clients (hub talks to voicecli adapters over NATS)
        stt_service = init_nats_stt(nc)
        await stt_service.start()
        tts_service = init_nats_tts(nc)
        await tts_service.start()
        nats_llm_driver = await init_nats_llm(nc)

        hub = build_hub(
            raw_config,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            pairing_manager=pm,
            stt_service=stt_service,
            tts_service=tts_service,
            prefs_store=stores.prefs,
            inbound_bus=inbound_bus,
            inbound_bus_cfg=inbound_bus_cfg,
        )
        hub.set_turn_store(stores.turn)
        hub.set_message_index(stores.message_index)

        cli_pool = await build_cli_pool(raw_config, agent_configs)
        if cli_pool is not None:
            cli_pool.set_turn_store(stores.turn)
        hub.cli_pool = cli_pool

        register_agents(
            hub,
            agent_configs,
            cli_pool,
            circuit_registry,
            msg_manager,
            stt_service,
            tts_service,
            stores.agent,
            raw_config,
            nats_llm_driver,
        )

        # Wire each (platform, bot_id) to a NatsChannelProxy + OutboundDispatcher
        tg_proxies, tg_dispatchers = wire_nats_telegram_proxies(
            hub=hub,
            nc=nc,
            tg_bot_auths=tg_bot_auths,
            bot_agent_map=bot_agent_map,
            circuit_registry=circuit_registry,
        )
        dc_proxies, dc_dispatchers = wire_nats_discord_proxies(
            hub=hub,
            nc=nc,
            dc_bot_auths=dc_bot_auths,
            bot_agent_map=bot_agent_map,
            circuit_registry=circuit_registry,
        )
        proxies = tg_proxies + dc_proxies
        dispatchers = tg_dispatchers + dc_dispatchers

        # Lifecycle: start buses, dispatchers, hub, health server
        await hub.inbound_bus.start()
        for d in dispatchers:
            await d.start()

        readiness_sub = await start_readiness_responder(nc, [hub.inbound_bus])

        import uvicorn

        health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
        health_app = create_health_app(hub, nc=nc)
        health_config = uvicorn.Config(
            health_app, host="127.0.0.1", port=health_port, log_level="warning"
        )
        health_server = uvicorn.Server(health_config)

        stop = _stop if _stop is not None else asyncio.Event()
        if _stop is None:
            setup_signal_handlers(stop)

        from lyra.bootstrap.factory.utils import watchdog

        tasks = [
            asyncio.create_task(hub.run(), name="hub"),
            asyncio.create_task(health_server.serve(), name="health"),
        ]

        if hub._event_bus is not None:
            from lyra.core.hub.pipeline.audit_consumer import AuditConsumer

            _audit_queue = hub._event_bus.subscribe()
            _audit_consumer = AuditConsumer(_audit_queue)
            tasks.append(
                asyncio.create_task(_audit_consumer.run(), name="audit-consumer")
            )

        active = [f"telegram:{c.bot_id}" for c, _ in tg_bot_auths] + [
            f"discord:{c.bot_id}" for c, _ in dc_bot_auths
        ]
        log.info(
            "Hub standalone started — NATS proxies: %s, health on :%d.",
            ", ".join(active) if active else "none",
            health_port,
        )

        await notify_startup(active, health_port)

        await watchdog(tasks, stop)

        log.info("Shutdown signal received — stopping...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await shutdown_hub_runtime(
            hub,
            readiness_sub=readiness_sub,
            dispatchers=dispatchers,
            proxies=proxies,
            pm=pm,
            cli_pool=cli_pool,
            nats_llm_driver=nats_llm_driver,
        )

    # Close NATS connection after stores context exits
    try:
        await nc.close()
        log.info("NATS connection closed.")
    except Exception as exc:
        log.warning("Error closing NATS connection: %s", exc)

    release_lockfile()
    log.info("Hub standalone stopped.")
