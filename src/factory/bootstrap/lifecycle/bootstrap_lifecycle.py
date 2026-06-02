"""Lifecycle orchestration for multibot bootstrap."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from factory.bootstrap.infra.health import create_health_app
from factory.bootstrap.lifecycle.lifecycle_helpers import (
    close_safely,
    setup_signal_handlers,
    teardown_buses,
    teardown_dispatchers,
)
from factory.bootstrap.types import LifecycleResources, WiredAdapters
from factory.core.hub import Hub

log = logging.getLogger(__name__)


async def run_lifecycle(  # noqa: C901 — DEBT:migration-sequence-bootstrap — lifecycle orchestration
    hub: Hub,
    wired: WiredAdapters,
    resources: LifecycleResources,
    _stop: asyncio.Event | None,
) -> None:
    """Start all buses/dispatchers/adapters, wait for stop, then tear down.

    When *resources.nc* is provided (unified mode with NATS), it is forwarded
    to the health endpoint so ``/health/detail`` can surface NATS reachability.
    """
    await hub.inbound_bus.start()
    for d in wired.tg_dispatchers:
        await d.start()
    for d in wired.dc_dispatchers:
        await d.start()

    health_port = int(os.environ.get("FACTORY_HEALTH_PORT", "8443"))
    health_host = os.environ.get("FACTORY_HEALTH_HOST", "127.0.0.1")
    health_app = create_health_app(hub, nc=resources.nc)
    health_config = uvicorn.Config(
        health_app, host=health_host, port=health_port, log_level="warning"
    )
    health_server = uvicorn.Server(health_config)

    stop = _stop if _stop is not None else asyncio.Event()
    if _stop is None:
        setup_signal_handlers(stop)

    from factory.bootstrap.factory.utils import watchdog

    tasks = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(health_server.serve(), name="health"),
    ]

    # Wire audit consumer for pipeline telemetry (#432).
    if hub._event_bus is not None:
        from factory.core.hub.pipeline.audit_consumer import AuditConsumer

        _audit_queue = hub._event_bus.subscribe()
        _audit_consumer = AuditConsumer(_audit_queue)
        _audit_task = asyncio.create_task(_audit_consumer.run(), name="audit-consumer")
        tasks.append(_audit_task)
    for tg_adapter in wired.tg_adapters:
        tasks.append(
            asyncio.create_task(
                tg_adapter.dp.start_polling(tg_adapter.bot, handle_signals=False),
                name=f"telegram:{tg_adapter._bot_id}",
            )
        )
    for dc_adapter, dc_bot_cfg, dc_token in wired.dc_adapters:
        _dc_task = asyncio.create_task(
            dc_adapter.start(dc_token),
            name=f"discord:{dc_bot_cfg.bot_id}",
        )
        tasks.append(_dc_task)

    tg_active = [f"telegram:{a._bot_id}" for a in wired.tg_adapters]
    dc_active = [f"discord:{c.bot_id}" for _, c, _ in wired.dc_adapters]
    active = tg_active + dc_active
    log.info(
        "Lyra started — adapters: %s, health on :%d.",
        ", ".join(active) if active else "none",
        health_port,
    )

    await watchdog(tasks, stop)

    log.info("Shutdown signal received — stopping…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await teardown_buses(hub.inbound_bus)
    await teardown_dispatchers(wired.tg_dispatchers + wired.dc_dispatchers)
    await close_safely(
        "typing-listeners",
        *[tl.stop() for tl in wired.tg_typing_listeners + wired.dc_typing_listeners],
    )
    # proxies is only populated in three-process hub_standalone mode; unified mode
    # runs adapters in-process (platform SDKs) and does not use NatsChannelProxy.
    for proxy in resources.proxies:
        await proxy.publish_stream_errors("hub_shutdown")
    await close_safely("dc-adapters", *[a.close() for a, _, _ in wired.dc_adapters])
    if wired.dc_thread_store is not None:
        await wired.dc_thread_store.close()
    if resources.pm is not None:
        await resources.pm.close()
    if resources.cli_pool is not None:
        await resources.cli_pool.drain(timeout=60.0)
        # Notify only sessions that couldn't finish within the drain window.
        active_ids = resources.cli_pool.get_active_pool_ids()
        if active_ids:
            await hub.notify_shutdown_inflight(active_ids)
        await resources.cli_pool.stop()
    await hub.shutdown()
    log.info("Lyra stopped.")
