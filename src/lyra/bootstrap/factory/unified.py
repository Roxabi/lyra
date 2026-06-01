"""Unified bootstrap — single-process hub + adapters with optional embedded NATS."""

from __future__ import annotations

import asyncio
import logging
import os

import nats.errors

import nats
from lyra.bootstrap.bootstrap_stores import open_stores
from lyra.bootstrap.factory.agent_factory import _init_bot_auths_and_agents
from lyra.bootstrap.factory.hub_builder import _build_hub, _init_clipool
from lyra.bootstrap.factory.voice_overlay import init_blobstore
from lyra.bootstrap.factory.wiring_helpers import (
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
from lyra.bootstrap.types import (
    BuildHubDeps,
    LifecycleResources,
    RegisterAgentsDeps,
    WireAdaptersDeps,
)
from lyra.paths import factory_data_dir

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
    blob_store = None
    try:
        inbound_bus = await _init_inbound_bus(nc, raw_config)
        vault_dir = factory_data_dir()
        vault_dir.mkdir(parents=True, exist_ok=True)

        async with open_stores(vault_dir, nc=nc) as stores:
            await _prune_message_index(stores, raw_config)
            await _seed_auth(stores)

            bundle = await _init_bot_auths_and_agents(stores, raw_config)
            pm = await _init_pairing(
                raw_config, bundle.admin_user_ids, vault_dir, stores
            )
            voice = await _init_voice_services(nc)
            blob_store = init_blobstore()
            hub = _build_hub(
                BuildHubDeps(
                    raw_config=raw_config,
                    bundle=bundle,
                    voice=voice,
                    inbound_bus=inbound_bus,
                    pm=pm,
                    stores=stores,
                    blob_store=blob_store,
                )
            )

            clipool = await _init_clipool(nc, raw_config, stores)
            hub.cli_pool = None  # hub no longer holds CliPool directly

            _register_agents(
                RegisterAgentsDeps(
                    hub=hub,
                    bundle=bundle,
                    voice=voice,
                    clipool=clipool,
                    raw_config=raw_config,
                    stores=stores,
                )
            )

            # ADR-079 unified-mode note: _wire_adapters uses bootstrap_wiring.py
            # (wire_telegram_adapters / wire_discord_adapters), which does NOT
            # start JetStreamAudioConsumer. Audio consumers are only started by
            # the standalone adapter path (standalone_telegram.py / _discord.py).
            # Therefore no audio provisioning barrier is required here.
            # Follow-up: wire audio consumers in unified mode if needed (#1521).
            wired = await _wire_adapters(
                WireAdaptersDeps(
                    hub=hub,
                    bundle=bundle,
                    nc=nc,
                    stores=stores,
                    vault_dir=vault_dir,
                    raw_config=raw_config,
                    blob_store=blob_store,
                )
            )

            clipool_worker_task = await _run_clipool_worker_task(clipool.worker, nc)

            resources = LifecycleResources(pm=pm, cli_pool=None, nc=nc)
            await run_lifecycle(hub, wired, resources, _stop)

            clipool_worker_task.cancel()
            await asyncio.gather(clipool_worker_task, return_exceptions=True)

    finally:
        if voice is not None and voice.nats_llm_client is not None:
            await voice.nats_llm_client.stop()
        if clipool is not None and clipool.cli_nats_driver is not None:
            await clipool.cli_nats_driver.stop()
        # Flush in-flight audit emit tasks before closing NATS (audit uses JetStream).
        if clipool is not None:
            await clipool.cli_pool.drain_audit_tasks()
        if blob_store is not None:
            await blob_store.aclose()  # type: ignore[union-attr]  # concrete HttpBlobStoreAdapter; aclose not on port
        try:
            await nc.close()
            log.info("NATS connection closed.")
        except nats.errors.Error as exc:
            log.warning("Error closing NATS connection: %s", exc)
        if embedded:
            await embedded.stop()
        release_lockfile()

    log.info("Lyra stopped.")
