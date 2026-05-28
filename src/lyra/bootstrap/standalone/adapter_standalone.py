"""Standalone adapter bootstrap — NATS-connected adapter without local Hub."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.composition_root import DependencyGraph, compose_adapter
from lyra.bootstrap.lifecycle.lifecycle_helpers import close_safely
from lyra.bootstrap.lifecycle.signal_handlers import setup_shutdown_event
from lyra.core.messaging.metrics import log_contracts_version
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url
from roxabi_nats.readiness import wait_for_hub

log = logging.getLogger(__name__)


async def _bootstrap_adapter_standalone(
    raw_config: dict,
    platform: str,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone adapter process connected to NATS.

    Args:
        raw_config: Parsed config dict (lyra config.toml content).
        platform: "telegram" or "discord".
        _stop: Optional event for graceful shutdown (tests inject this).
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL required for standalone adapter mode")

    log_contracts_version()

    try:
        nc = await nats_connect(nats_url, identity_name=f"{platform}-adapter")
        log.info(
            "adapter_standalone: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir.mkdir(parents=True, exist_ok=True)

    try:
        deps = DependencyGraph()
        subsystem = await compose_adapter(raw_config, nc, platform, vault_dir, deps)

        await wait_for_hub(nc)

        if platform == "telegram":
            stop = setup_shutdown_event(_stop)

            poll_tasks = [
                asyncio.create_task(
                    entry.adapter.dp.start_polling(
                        entry.adapter.bot, handle_signals=False
                    ),
                    name=f"telegram:{entry.adapter._bot_id}",
                )
                for entry in subsystem.entries
            ]
            try:
                await stop.wait()
                for entry in subsystem.entries:
                    await entry.adapter.dp.stop_polling()
                await asyncio.gather(*poll_tasks, return_exceptions=True)
            finally:
                await close_safely(
                    "tg",
                    *[
                        coro
                        for entry in subsystem.entries
                        for coro in (
                            entry.adapter.close(),
                            entry.inbound_bus.stop(),
                            entry.typing_listener.stop(),
                        )
                    ],
                )
                if subsystem.turn_store is not None:
                    await subsystem.turn_store.close()

        elif platform == "discord":
            stop_dc = setup_shutdown_event(_stop)
            start_tasks = [
                asyncio.create_task(
                    entry.adapter.start(entry.token),
                    name=f"discord:{entry.adapter._bot_id}",
                )
                for entry in subsystem.entries
            ]
            try:
                await stop_dc.wait()
                await close_safely(
                    "dc-adapters",
                    *[entry.adapter.close() for entry in subsystem.entries],
                )
                for t in start_tasks:
                    t.cancel()
                await asyncio.gather(*start_tasks, return_exceptions=True)
            finally:
                dc_bus_coros = [
                    entry.inbound_bus.stop() for entry in subsystem.entries
                ]
                await close_safely("dc-buses", *dc_bus_coros)
                await close_safely(
                    "dc-typing",
                    *[entry.typing_listener.stop() for entry in subsystem.entries],
                )
                if subsystem.thread_store is not None:
                    await subsystem.thread_store.close()
                if subsystem.turn_store is not None:
                    await subsystem.turn_store.close()

        else:
            sys.exit(f"Unknown platform: {platform!r}")
    finally:
        await nc.close()
