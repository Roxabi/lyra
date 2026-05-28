"""Bootstrap standalone Hub — NATS-connected Hub without embedded adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from lyra.bootstrap.bootstrap_stores import StoreBundle, open_stores
from lyra.bootstrap.composition_root import DependencyGraph, compose_hub
from lyra.bootstrap.infra.health import create_health_app
from lyra.bootstrap.infra.lockfile import acquire_lockfile, release_lockfile
from lyra.bootstrap.infra.notify import notify_startup
from lyra.bootstrap.lifecycle.lifecycle_helpers import setup_signal_handlers
from lyra.bootstrap.standalone.hub_standalone_helpers import (
    shutdown_hub_runtime,
    start_mint_failure_subscriber,
)
from lyra.core.messaging.metrics import log_contracts_version
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url
from roxabi_nats.readiness import announce_hub_ready, start_readiness_responder

log = logging.getLogger(__name__)


async def _bootstrap_hub_standalone(  # noqa: C901, PLR0915 — DEBT:migration-sequence-bootstrap
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

    # Drivers built later are registered here so the reconnect callback can
    # clear their stale freshness timestamps after a NATS reconnect.
    _freshness_drivers: list[Any] = []

    async def _on_nats_reconnect() -> None:
        log.info("NATS reconnected — clearing worker freshness caches")
        for _d in _freshness_drivers:
            if hasattr(_d, "_worker_freshness"):
                _d._worker_freshness.clear()

    try:
        nc = await nats_connect(
            nats_url, identity_name="hub", reconnected_cb=_on_nats_reconnect
        )
        log.info("Connected to NATS at %s", scrub_nats_url(nats_url))
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    vault_dir = Path(
        os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))
    ).resolve()
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with open_stores(vault_dir) as stores:
        deps = DependencyGraph()
        deps.register(StoreBundle, stores)

        subsystem = await compose_hub(raw_config, nc, vault_dir, deps)

        _freshness_drivers.extend(
            d
            for d in [subsystem.cli_nats_driver, subsystem.nats_llm_client]
            if d is not None
        )

        # Lifecycle: start buses, dispatchers, hub, health server
        await subsystem.hub.inbound_bus.start()
        for d in subsystem.tg_dispatchers + subsystem.dc_dispatchers:
            await d.start()

        mint_failure_sub = await start_mint_failure_subscriber(nc)

        await announce_hub_ready(nc)
        readiness_sub = await start_readiness_responder(nc, [subsystem.hub.inbound_bus])

        import uvicorn

        health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
        health_host = os.environ.get("LYRA_HEALTH_HOST", "127.0.0.1")
        health_app = create_health_app(subsystem.hub, nc=nc)
        health_config = uvicorn.Config(
            health_app, host=health_host, port=health_port, log_level="warning"
        )
        health_server = uvicorn.Server(health_config)

        stop = _stop if _stop is not None else asyncio.Event()
        if _stop is None:
            setup_signal_handlers(stop)

        from lyra.bootstrap.factory.utils import watchdog

        tasks = [
            asyncio.create_task(subsystem.hub.run(), name="hub"),
            asyncio.create_task(health_server.serve(), name="health"),
        ]

        if subsystem.hub._event_bus is not None:
            from lyra.core.hub.pipeline.audit_consumer import AuditConsumer

            _audit_queue = subsystem.hub._event_bus.subscribe()
            _audit_consumer = AuditConsumer(_audit_queue)
            tasks.append(
                asyncio.create_task(_audit_consumer.run(), name="audit-consumer")
            )

        active = [f"telegram:{c.bot_id}" for c, _ in subsystem.tg_bot_auths] + [
            f"discord:{c.bot_id}" for c, _ in subsystem.dc_bot_auths
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
        if mint_failure_sub is not None:
            await mint_failure_sub.stop()
        await shutdown_hub_runtime(
            subsystem.hub,
            readiness_sub=readiness_sub,
            dispatchers=subsystem.tg_dispatchers + subsystem.dc_dispatchers,
            proxies=subsystem.tg_proxies + subsystem.dc_proxies,
            pm=subsystem.pairing_manager,
            cli_nats_driver=subsystem.cli_nats_driver,
            nats_llm_client=subsystem.nats_llm_client,
        )

    # Close NATS connection after stores context exits
    try:
        await nc.close()
        log.info("NATS connection closed.")
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        log.warning("Error closing NATS connection: %s", exc)

    release_lockfile()
    log.info("Hub standalone stopped.")
