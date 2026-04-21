"""Lifecycle orchestration for multibot bootstrap."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import uvicorn

from lyra.bootstrap.health import create_health_app
from lyra.bootstrap.lifecycle_helpers import (
    setup_signal_handlers,
    teardown_buses,
    teardown_dispatchers,
)
from lyra.core.cli.cli_pool import CliPool
from lyra.core.hub import Hub
from lyra.core.stores.pairing import PairingManager
from lyra.nats.nats_channel_proxy import NatsChannelProxy

log = logging.getLogger(__name__)


async def run_lifecycle(  # noqa: PLR0913, C901 — lifecycle orchestration
    hub: Hub,
    tg_adapters: list,
    tg_dispatchers: list,
    dc_adapters: list[tuple],
    dc_dispatchers: list,
    pm: PairingManager | None,
    cli_pool: CliPool | None,
    _stop: asyncio.Event | None,
    proxies: list[NatsChannelProxy] | None = None,
    nc: Any | None = None,
) -> None:
    """Start all buses/dispatchers/adapters, wait for stop, then tear down.

    When *nc* is provided (unified mode with NATS), it is forwarded to the
    health endpoint so ``/health/detail`` can surface NATS reachability.
    """
    await hub.inbound_bus.start()
    for d in tg_dispatchers:
        await d.start()
    for d in dc_dispatchers:
        await d.start()

    health_port = int(os.environ.get("LYRA_HEALTH_PORT", "8443"))
    health_app = create_health_app(hub, nc=nc)
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

    # Wire audit consumer for pipeline telemetry (#432).
    if hub._event_bus is not None:
        from lyra.core.hub.audit_consumer import AuditConsumer

        _audit_queue = hub._event_bus.subscribe()
        _audit_consumer = AuditConsumer(_audit_queue)
        _audit_task = asyncio.create_task(_audit_consumer.run(), name="audit-consumer")
        tasks.append(_audit_task)
    for tg_adapter in tg_adapters:
        tasks.append(
            asyncio.create_task(
                tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
                name=f"telegram:{tg_adapter._bot_id}",
            )
        )
    for dc_adapter, dc_bot_cfg, dc_token in dc_adapters:
        _dc_task = asyncio.create_task(
            dc_adapter.start(dc_token),
            name=f"discord:{dc_bot_cfg.bot_id}",
        )
        tasks.append(_dc_task)

    tg_active = [f"telegram:{a._bot_id}" for a in tg_adapters]
    dc_active = [f"discord:{c.bot_id}" for _, c, _ in dc_adapters]
    active = tg_active + dc_active
    log.info(
        "Lyra started \u2014 adapters: %s, health on :%d.",
        ", ".join(active) if active else "none",
        health_port,
    )

    await watchdog(tasks, stop)

    log.info("Shutdown signal received \u2014 stopping\u2026")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await teardown_buses(hub.inbound_bus)
    await teardown_dispatchers(tg_dispatchers + dc_dispatchers)
    # proxies is only populated in three-process hub_standalone mode; unified mode
    # runs adapters in-process (platform SDKs) and does not use NatsChannelProxy.
    for proxy in proxies or []:
        await proxy.publish_stream_errors("hub_shutdown")
    for dc_adapter, _, _dc_tok in dc_adapters:
        await dc_adapter.close()
    if pm is not None:
        await pm.close()
    if cli_pool is not None:
        await cli_pool.drain(timeout=60.0)
        # Notify only sessions that couldn't finish within the drain window.
        active_ids = cli_pool.get_active_pool_ids()
        if active_ids:
            await hub.notify_shutdown_inflight(active_ids)
        await cli_pool.stop()
    await hub.shutdown()
    log.info("Lyra stopped.")
