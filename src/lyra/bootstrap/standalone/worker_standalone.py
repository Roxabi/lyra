"""Bootstrap standalone workers — CliPool and TurnWriter."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker
from lyra.bootstrap.factory.config import _load_cli_pool_config
from lyra.bootstrap.infra.git_ownership_probe import run_git_ownership_probe
from lyra.core.cli.cli_pool import CliPool, CliPoolDeps
from lyra.core.messaging.utils.metrics import log_contracts_version
from lyra.infrastructure.stores.turn_store import TurnStore
from lyra.infrastructure.turn_writer.health import TurnWriterHealthServer
from lyra.infrastructure.turn_writer.stream_setup import (
    ensure_consumer,
    ensure_stream,
)
from lyra.infrastructure.turn_writer.writer import TurnWriter
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

    worker = CliPoolNatsWorker(
        cli_pool,
        timeout=cli_pool_cfg.default_timeout,
        identity_name="clipool-worker",
    )
    log.info("clipool: starting CliPoolNatsWorker on lyra.clipool.cmd")
    try:
        await worker.run(nats_url)
    finally:
        await cli_pool.drain_audit_tasks()
        await cli_pool.stop()


async def _bootstrap_turn_writer_standalone(raw_config: dict) -> None:
    """Run the standalone TurnWriter process until SIGTERM/SIGINT.

    Steps: open NATS → ensure stream/consumer → open TurnStore (rw) →
    start TurnWriter → wait shutdown → stop writer → close store.

    Args:
        raw_config: Parsed config dict (lyra config.toml content).
    """
    from lyra.bootstrap.lifecycle.signal_handlers import setup_shutdown_event

    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL is required for standalone turn-writer mode.")

    db_path = Path(
        os.environ.get("LYRA_TURNS_DB")
        or (
            Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
            / "turns.db"
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    log.info(
        "turn-writer: starting (db=%s, nats=%s)",
        db_path,
        scrub_nats_url(nats_url),
    )

    try:
        nc = await nats_connect(nats_url, identity_name="turn-writer")
        log.info(
            "turn-writer: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    js = nc.jetstream()

    await ensure_stream(js)
    await ensure_consumer(js)

    store = TurnStore(db_path=db_path)
    await store.connect()

    writer = TurnWriter(store, js)
    await writer.start()

    health_host = os.environ.get("LYRA_TURN_WRITER_HEALTH_HOST", "0.0.0.0")
    health_port = int(os.environ.get("LYRA_TURN_WRITER_HEALTH_PORT", "8083"))
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
        await nc.close()
        log.info("turn-writer: stopped cleanly")
