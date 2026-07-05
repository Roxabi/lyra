"""Bootstrap standalone workers — CliPool and TurnWriter."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import nats.errors

from factory.adapters.clipool.clipool_worker import CliPoolNatsWorker
from factory.bootstrap.factory.config import _load_cli_pool_config
from factory.bootstrap.infra.git_ownership_probe import run_git_ownership_probe
from factory.bootstrap.lifecycle.signal_handlers import setup_shutdown_event
from factory.core.cli.cli_pool import CliPool, CliPoolDeps
from factory.core.messaging.utils.metrics import log_contracts_version
from factory.infrastructure.stores.session.turn_store import TurnStore
from factory.infrastructure.turn_writer.health import TurnWriterHealthServer
from factory.infrastructure.turn_writer.stream_setup import (
    ensure_consumer,
    ensure_stream,
)
from factory.infrastructure.turn_writer.writer import TurnWriter
from factory.paths import factory_turns_db_path
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url

log = logging.getLogger(__name__)


def _export_secret_file(file_var: str, value_var: str) -> None:
    """Bridge a Podman type=mount secret file into a plain env var.

    omp_rpc resolves models.yml `apiKey: LITELLM_API_KEY` from the process
    environment, but the key is delivered as a tmpfs secret file referenced by
    *file_var* (LITELLM_API_KEY_FILE) per the deploy/ secret-at-rest discipline.
    Read the file once at boot and export *value_var* in-process only — never
    persisted to disk or the unit file. No-op when *file_var* is unset (local/dev
    omp uses the default localhost provider with no key).
    """
    path = os.environ.get(file_var)
    if not path:
        return
    value = Path(path).read_text().strip()
    if not value:
        sys.exit(f"{file_var} points at an empty secret file: {path}")
    os.environ[value_var] = value


async def _bootstrap_clipool_standalone(raw_config: dict) -> None:
    """Wire a standalone CliPoolNatsWorker connected to NATS."""
    run_git_ownership_probe()
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL is required for standalone clipool mode.")

    log_contracts_version()

    cli_pool_cfg = _load_cli_pool_config(raw_config)

    log.info("clipool: will connect to NATS at %s", scrub_nats_url(nats_url))

    cli_pool = CliPool(
        CliPoolDeps(
            idle_ttl=cli_pool_cfg.idle_ttl,
            default_timeout=cli_pool_cfg.default_timeout,
            reaper_interval=cli_pool_cfg.reaper_interval,
            kill_timeout=cli_pool_cfg.kill_timeout,
            read_buffer_bytes=cli_pool_cfg.read_buffer_bytes,
            stdin_drain_timeout=cli_pool_cfg.stdin_drain_timeout,
            max_idle_retries=cli_pool_cfg.max_idle_retries,
            intermediate_timeout=cli_pool_cfg.intermediate_timeout,
        )
    )
    await cli_pool.start()

    from factory.obs.otel_wiring import build_lifecycle_hooks

    worker = CliPoolNatsWorker(
        cli_pool,
        timeout=cli_pool_cfg.default_timeout,
        identity_name="clipool-worker",
        lifecycle_hooks=build_lifecycle_hooks("clipool-workers"),
    )
    log.info("clipool: starting CliPoolNatsWorker on factory.jobs.claude")
    from factory.bootstrap.fleet_reporter import (
        cancel_fleet_reporter,
        start_fleet_reporter,
    )

    nc = None
    fleet_reporter_task: asyncio.Task[None] | None = None
    try:
        nc = await nats_connect(nats_url, identity_name="clipool-worker")
        fleet_reporter_task = await start_fleet_reporter(nc)
        stop = setup_shutdown_event()
        await worker.run_embedded(nc, stop)
    finally:
        await cancel_fleet_reporter(fleet_reporter_task)
        if nc is not None:
            await nc.close()
        await cli_pool.drain_audit_tasks()
        await cli_pool.stop()


async def _bootstrap_turn_writer_standalone(raw_config: dict) -> None:
    """Run the standalone TurnWriter process until SIGTERM/SIGINT.

    Steps: open NATS → ensure stream/consumer → open TurnStore (rw) →
    start TurnWriter → wait shutdown → stop writer → close store.

    Args:
        raw_config: Parsed config dict (factory config.toml content).
    """
    from factory.bootstrap.lifecycle.signal_handlers import setup_shutdown_event

    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL is required for standalone turn-writer mode.")

    db_path = factory_turns_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(
        "turn-writer: starting (db=%s, nats=%s)",
        db_path,
        scrub_nats_url(nats_url),
    )

    from factory.bootstrap.fleet_reporter import (
        cancel_fleet_reporter,
        start_fleet_reporter,
    )

    fleet_reporter_task: asyncio.Task[None] | None = None
    try:
        nc = await nats_connect(nats_url, identity_name="turn-writer")
        fleet_reporter_task = await start_fleet_reporter(nc)
        log.info(
            "turn-writer: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except (nats.errors.Error, OSError) as exc:
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    js = nc.jetstream()

    await ensure_stream(js)
    await ensure_consumer(js)

    store = TurnStore(db_path=db_path)
    await store.connect()

    writer = TurnWriter(store, js)
    await writer.start()

    health_host = os.environ.get("FACTORY_TURN_WRITER_HEALTH_HOST", "0.0.0.0")
    health_port = int(os.environ.get("FACTORY_TURN_WRITER_HEALTH_PORT", "8083"))
    health_server = TurnWriterHealthServer(writer, store, nc, health_host, health_port)
    await health_server.start()

    stop = setup_shutdown_event()

    try:
        await stop.wait()
    finally:
        log.info("turn-writer: stopping")
        await health_server.stop()
        await writer.stop()
        await store.close()
        await cancel_fleet_reporter(fleet_reporter_task)
        await nc.close()
        log.info("turn-writer: stopped cleanly")


async def _bootstrap_omp_standalone(raw_config: dict) -> None:  # noqa: ARG001
    """Wire a standalone OmpWorker connected to NATS.

    omp_rpc is a container image dep (absent from pyproject.toml).
    The digest gate inside RpcBridge.__init__ will raise DigestMismatchError
    if the binary does not match the pinned SHA-256.
    """
    _export_secret_file("LITELLM_API_KEY_FILE", "LITELLM_API_KEY")
    run_git_ownership_probe()
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL is required for standalone omp mode.")

    log_contracts_version()

    # omp_rpc is a container image dep — deferred imports prevent ImportError
    # in dev environments where the binary is absent from pyproject.toml.
    from factory.adapters.omp.omp_pool import OmpPool
    from factory.adapters.omp.omp_worker import OmpWorker

    pool = OmpPool()
    from factory.obs.otel_wiring import build_lifecycle_hooks

    worker = OmpWorker(
        pool=pool,
        identity_name="omp-worker",
        lifecycle_hooks=build_lifecycle_hooks("omp-workers"),
    )
    log.info("omp: starting OmpWorker on factory.jobs.omp")
    from factory.bootstrap.fleet_reporter import (
        cancel_fleet_reporter,
        start_fleet_reporter,
    )

    nc = await nats_connect(nats_url, identity_name="omp-worker")
    fleet_reporter_task: asyncio.Task[None] | None = None
    try:
        fleet_reporter_task = await start_fleet_reporter(nc)
        stop = setup_shutdown_event()
        await worker.run_embedded(nc, stop)
    finally:
        await cancel_fleet_reporter(fleet_reporter_task)
        await nc.close()


async def _bootstrap_log_monitor_standalone(raw_config: dict) -> None:  # noqa: ARG001
    """Run the standalone log-monitor loop (V1-pull detection, #2245).

    No NATS connection — this is a periodic pull-based health probe, not an
    event/metric bus producer (ADR-091 plane③ V1: pull; V2 subscribe is #1035,
    future work). Runs until SIGTERM/SIGINT, or once if FACTORY_LOG_MONITOR_ONCE is set.
    """
    from factory.monitoring.config import load_monitoring_config
    from factory.monitoring.log_watch import LogMonitorLoop

    config = load_monitoring_config()
    loop = LogMonitorLoop(config)

    log.info("log-monitor: starting (interval=%dm)", config.check_interval_minutes)

    if os.environ.get("FACTORY_LOG_MONITOR_ONCE"):
        report = await loop.run_once()
        sys.exit(0 if report.all_passed else 1)

    await loop.run_forever()
