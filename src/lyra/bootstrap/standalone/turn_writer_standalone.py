"""Standalone bootstrap for the lyra-turn-writer process.

Subscribes to lyra.turns.write (JetStream durable consumer), persists turn
events to ~/.lyra/turns.db via TurnStore private mutators.

This is the SOLE writer of turns.db post-refactor — hub + adapters mount the
file read-only.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from lyra.infrastructure.stores.turn_store import TurnStore
from lyra.infrastructure.turn_writer.stream_setup import (
    ensure_consumer,
    ensure_stream,
)
from lyra.infrastructure.turn_writer.writer import TurnWriter
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url

log = logging.getLogger(__name__)


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

    vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir.mkdir(parents=True, exist_ok=True)
    db_path = vault_dir / "turns.db"

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

    stop = setup_shutdown_event()

    try:
        await stop.wait()
    finally:
        log.info("turn-writer: stopping")
        await writer.stop()
        await store.close()
        await nc.close()
        log.info("turn-writer: stopped cleanly")
