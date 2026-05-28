"""Bootstrap standalone CliPool NATS worker."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from lyra.bootstrap.composition_root import DependencyGraph, compose_clipool
from lyra.bootstrap.infra.git_ownership_probe import run_git_ownership_probe
from lyra.core.messaging.metrics import log_contracts_version
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url

log = logging.getLogger(__name__)


async def _bootstrap_clipool_standalone(raw_config: dict) -> None:
    """Wire a standalone CliPoolNatsWorker connected to NATS."""
    run_git_ownership_probe()
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL is required for standalone clipool mode.")

    log_contracts_version()

    try:
        nc = await nats_connect(nats_url, identity_name="clipool-worker")
        log.info("clipool: connected to NATS at %s", scrub_nats_url(nats_url))
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    deps = DependencyGraph()
    subsystem = await compose_clipool(raw_config, nc, deps)

    log.info("clipool: starting CliPoolNatsWorker on lyra.clipool.cmd")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await subsystem.worker.run_embedded(nc, stop)
    finally:
        await subsystem.cli_pool.drain_audit_tasks()
        await subsystem.cli_pool.stop()
        try:
            await nc.close()
            log.info("NATS connection closed.")
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.warning("Error closing NATS connection: %s", exc)
