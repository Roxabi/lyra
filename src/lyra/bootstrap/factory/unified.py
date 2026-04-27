"""Unified bootstrap — single-process hub + adapters with optional embedded NATS."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from lyra.bootstrap.bootstrap_stores import open_stores
from lyra.bootstrap.factory.wiring_helpers import (
    _build_hub,
    _init_bot_auths_and_agents,
    _init_clipool,
    _init_inbound_bus,
    _init_pairing,
    _init_voice_services,
    _prune_message_index,
    _register_agents,
    _run_clipool_worker_task,
    _seed_auth,
    _wire_adapters,
)
from lyra.bootstrap.infra.embedded_nats import ensure_nats
from lyra.bootstrap.infra.lockfile import acquire_lockfile, release_lockfile
from lyra.bootstrap.lifecycle.bootstrap_lifecycle import run_lifecycle

log = logging.getLogger(__name__)


async def _bootstrap_unified(
    raw_config: dict,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Wire hub + adapters in one process with NATS (embedded or external)."""
    nc, embedded, _ = await ensure_nats(os.environ.get("NATS_URL"))
    acquire_lockfile()
    voice = None
    clipool = None
    try:
        inbound_bus = await _init_inbound_bus(nc, raw_config)
        vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
        vault_dir.mkdir(parents=True, exist_ok=True)

        async with open_stores(vault_dir) as stores:
            await _prune_message_index(stores, raw_config)
            await _seed_auth(stores, raw_config)

            bundle = await _init_bot_auths_and_agents(stores, raw_config)
            pm = await _init_pairing(
                raw_config, bundle.admin_user_ids, vault_dir, stores
            )
            voice = await _init_voice_services(nc)
            hub = _build_hub(raw_config, bundle, voice, inbound_bus, pm, stores)

            clipool = await _init_clipool(nc, raw_config, stores)
            hub.cli_pool = None  # hub no longer holds CliPool directly

            _register_agents(hub, bundle, voice, clipool, raw_config, stores)

            (
                tg_adapters,
                tg_dispatchers,
                dc_adapters,
                dc_dispatchers,
            ) = await _wire_adapters(hub, bundle, nc, stores, vault_dir)

            clipool_worker_task = await _run_clipool_worker_task(clipool.worker, nc)

            await run_lifecycle(
                hub,
                tg_adapters,
                tg_dispatchers,
                dc_adapters,
                dc_dispatchers,
                pm,
                None,
                _stop,
                nc=nc,
            )

            clipool_worker_task.cancel()
            await asyncio.gather(clipool_worker_task, return_exceptions=True)

    finally:
        if voice is not None and voice.nats_llm_driver is not None:
            await voice.nats_llm_driver.stop()
        if clipool is not None and clipool.cli_nats_driver is not None:
            await clipool.cli_nats_driver.stop()
        # Flush in-flight audit emit tasks before closing NATS (audit uses JetStream).
        if clipool is not None:
            await clipool.cli_pool.drain_audit_tasks()
        try:
            await nc.close()
            log.info("NATS connection closed.")
        except Exception as exc:
            log.warning("Error closing NATS connection: %s", exc)
        if embedded:
            await embedded.stop()
        release_lockfile()

    log.info("Lyra stopped.")
