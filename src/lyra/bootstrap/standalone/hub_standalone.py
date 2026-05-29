"""Bootstrap standalone Hub — NATS-connected Hub without embedded adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.auth_seeding import build_bot_auths, seed_grants_from_bots
from lyra.bootstrap.bootstrap_stores import open_stores
from lyra.bootstrap.factory.agent_factory import _resolve_bot_agent_map
from lyra.bootstrap.factory.config import MessageIndexConfig
from lyra.bootstrap.factory.hub_builder import _build_hub_and_wire, build_inbound_bus
from lyra.bootstrap.infra.health import create_health_server
from lyra.bootstrap.infra.lockfile import acquire_lockfile, release_lockfile
from lyra.bootstrap.infra.notify import notify_startup
from lyra.bootstrap.lifecycle.lifecycle_helpers import (
    _freshness_drivers,
    _on_nats_reconnect,
    _run_shutdown,
    setup_signal_handlers,
)
from lyra.bootstrap.standalone.hub_standalone_helpers import (
    _build_active_list,
    _create_hub_tasks,
    build_pairing_manager,
    load_agent_configs,
    start_mint_failure_subscriber,
)
from lyra.core.messaging.metrics import log_contracts_version
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url
from roxabi_nats.readiness import announce_hub_ready, start_readiness_responder

log = logging.getLogger(__name__)


async def _bootstrap_hub_standalone(  # noqa: C901, PLR0915 — DEBT:migration-sequence-bootstrap — startup wiring
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
    log_contracts_version()

    _freshness_drivers_list = _freshness_drivers()
    try:
        nc = await nats_connect(
            nats_url,
            identity_name="hub",
            reconnected_cb=_on_nats_reconnect(_freshness_drivers_list),
        )
        log.info("Connected to NATS at %s", scrub_nats_url(nats_url))
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    inbound_bus, _ = build_inbound_bus(nc, raw_config)

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

        await seed_grants_from_bots(stores.auth, stores.bot)

        try:
            circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths = (
                build_bot_auths(
                    raw_config, stores.auth, stores.bot, stores.identity_alias
                )
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

        hub_result = await _build_hub_and_wire(
            nc,
            raw_config,
            stores,
            circuit_registry=circuit_registry,
            bot_agent_map=bot_agent_map,
            msg_manager=msg_manager,
            pm=pm,
            inbound_bus=inbound_bus,
            freshness_drivers_list=_freshness_drivers_list,
            agent_configs=agent_configs,
            tg_bot_auths=tg_bot_auths,
            dc_bot_auths=dc_bot_auths,
            admin_user_ids=admin_user_ids,
        )
        hub, proxies, dispatchers, cli_nats_driver, nats_llm_client = hub_result

        # Lifecycle: start buses, dispatchers, hub, health server
        await hub.inbound_bus.start()
        for d in dispatchers:
            await d.start()

        mint_failure_sub = await start_mint_failure_subscriber(nc)

        await announce_hub_ready(nc)
        readiness_sub = await start_readiness_responder(nc, [hub.inbound_bus])

        health_server, health_port = create_health_server(hub, nc=nc)

        stop = _stop if _stop is not None else asyncio.Event()
        if _stop is None:
            setup_signal_handlers(stop)

        tasks = _create_hub_tasks(hub, health_server)

        active = _build_active_list(tg_bot_auths, dc_bot_auths)
        log.info(
            "Hub standalone started — NATS proxies: %s, health on :%d.",
            ", ".join(active) if active else "none",
            health_port,
        )

        await notify_startup(active, health_port)

        await _run_shutdown(
            tasks,
            stop,
            mint_failure_sub,
            hub,
            readiness_sub=readiness_sub,
            dispatchers=dispatchers,
            proxies=proxies,
            pm=pm,
            cli_nats_driver=cli_nats_driver,
            nats_llm_client=nats_llm_client,
        )

    # Close NATS connection after stores context exits
    try:
        await nc.close()
        log.info("NATS connection closed.")
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        log.warning("Error closing NATS connection: %s", exc)

    release_lockfile()
    log.info("Hub standalone stopped.")
