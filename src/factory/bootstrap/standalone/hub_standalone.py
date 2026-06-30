"""Bootstrap standalone Hub — NATS-connected Hub without embedded adapters."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import nats.errors

from factory.bootstrap.auth_seeding import build_bot_auths
from factory.bootstrap.bootstrap_stores import open_stores
from factory.bootstrap.factory.agent_factory import _resolve_bot_agent_map
from factory.bootstrap.factory.config import MessageIndexConfig
from factory.bootstrap.factory.hub_builder import _build_hub_and_wire, build_inbound_bus
from factory.bootstrap.infra.health import create_health_server
from factory.bootstrap.infra.lockfile import acquire_lockfile, release_lockfile
from factory.bootstrap.infra.notify import notify_startup
from factory.bootstrap.lifecycle.lifecycle_helpers import (
    _freshness_drivers,
    _on_nats_reconnect,
    _run_shutdown,
    setup_signal_handlers,
)
from factory.bootstrap.standalone.hub_standalone_helpers import (
    _build_active_list,
    _create_hub_tasks,
    build_pairing_manager,
    load_agent_configs,
    start_mint_failure_subscriber,
)
from factory.bootstrap.wiring.kv_agent_roster import publish_agent_roster
from factory.bootstrap.wiring.kv_bot_roster import publish_bot_roster
from factory.bootstrap.wiring.kv_watch_channels import publish_watch_channels
from factory.core.messaging.utils.metrics import log_contracts_version
from factory.paths import factory_data_dir
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url
from roxabi_nats.readiness import announce_hub_ready, start_readiness_responder

log = logging.getLogger(__name__)


async def _bootstrap_hub_standalone(  # noqa: C901, PLR0915 — DEBT:migration-sequence-bootstrap — startup wiring
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

    _freshness_drivers_list = _freshness_drivers()
    try:
        nc = await nats_connect(
            nats_url,
            identity_name="hub",
            reconnected_cb=_on_nats_reconnect(_freshness_drivers_list),
        )
        log.info("Connected to NATS at %s", scrub_nats_url(nats_url))
    except (nats.errors.Error, OSError) as exc:
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    inbound_bus, _ = build_inbound_bus(nc, raw_config)

    vault_dir = factory_data_dir().resolve()
    vault_dir.mkdir(parents=True, exist_ok=True)

    async with open_stores(vault_dir, nc=nc) as stores:
        # Prune stale message_index entries
        mi_cfg = MessageIndexConfig(**raw_config.get("message_index", {}))
        pruned = await stores.message_index.cleanup_older_than(mi_cfg.retention_days)
        if pruned:
            log.info(
                "message_index: pruned %d entries older than %d days",
                pruned,
                mi_cfg.retention_days,
            )

        from factory.bootstrap.factory.config import _load_circuit_config

        _, admin_user_ids = _load_circuit_config(raw_config)

        try:
            circuit_registry, admin_user_ids, tg_bot_auths, dc_bot_auths = (
                build_bot_auths(
                    raw_config, stores.auth, stores.bot, stores.identity_alias
                )
            )
        except ValueError as exc:
            log.error("Configuration error: %s", exc)
            sys.exit(str(exc))

        # Resolve (platform, bot_id) -> agent_name
        bot_agent_map = await _resolve_bot_agent_map(
            stores.agent,
            [cfg for cfg, _ in tg_bot_auths],
            [cfg for cfg, _ in dc_bot_auths],
        )

        from factory.bootstrap.factory.voice_overlay import init_blobstore

        blob_store = init_blobstore()
        agent_configs = await load_agent_configs(
            stores.agent,
            raw_config,
            set(bot_agent_map.values()),
            blob_store=blob_store,
        )
        if not agent_configs:
            sys.exit(
                "No agent configs could be loaded — run 'factory agent init' "
                "to seed the agents table"
            )
        first_agent_config = agent_configs[next(iter(sorted(agent_configs)))]

        from factory.bootstrap.factory.config import _load_messages

        msg_manager = _load_messages(language=first_agent_config.i18n_language)

        pm = await build_pairing_manager(
            raw_config,
            vault_dir=vault_dir,
            grant_store=stores.grant,
            user_store=stores.user,
            admin_user_ids=admin_user_ids,
        )

        hub_result = await _build_hub_and_wire(
            nc,
            raw_config,
            stores,
            circuit_registry=circuit_registry,
            bot_agent_map=bot_agent_map,
            msg_manager=msg_manager,
            pm=pm,
            inbound_bus=inbound_bus,
            freshness_drivers_list=_freshness_drivers_list,
            agent_configs=agent_configs,
            tg_bot_auths=tg_bot_auths,
            dc_bot_auths=dc_bot_auths,
            admin_user_ids=admin_user_ids,
        )
        hub, proxies, dispatchers, cli_nats_driver, nats_llm_client = hub_result

        # Lifecycle: start buses, dispatchers, hub, health server
        await hub.inbound_bus.start()
        for d in dispatchers:
            await d.start()

        mint_failure_sub = await start_mint_failure_subscriber(nc)

        # Provision shared audio infrastructure before signalling readiness.
        # Adapters block on wait_for_hub; stream + KV are guaranteed to exist
        # when they connect. Idempotent: safe on every hub restart. ADR-079.
        # Fail-fast: provisioning is terminal — adapters block on wait_for_hub
        # until stream+KV exist (ADR-079 S3). RestartSec recovers the hub.
        from factory.infrastructure.outbound_audio.stream_setup import (
            ensure_kv,
            ensure_stream,
        )

        _audio_js = nc.jetstream()
        try:
            await ensure_stream(_audio_js)
            await ensure_kv(_audio_js)
        except nats.errors.Error as exc:
            log.critical(
                "hub_standalone: audio provisioning failed — stream/KV not created;"
                " hub cannot announce ready; adapters will not unblock. "
                "Cause: %s. RestartSec will recover. (ADR-079 S3)",
                exc,
            )
            raise

        from factory.infrastructure.audit import JetStreamAuditSink

        _audit_sink = JetStreamAuditSink()
        await _audit_sink.provision(nc)
        if _audit_sink._degraded:  # noqa: SLF001
            log.critical(
                "hub_standalone: FACTORY_AUDIT stream provision failed — "
                "security audit degraded for this process lifetime. "
                "RestartSec will recover."
            )
            raise RuntimeError("FACTORY_AUDIT provision failed")

        from factory.infrastructure.jobs.stream_setup import ensure_jobs_stream

        try:
            await ensure_jobs_stream(_audio_js)
        except nats.errors.Error as exc:
            log.critical(
                "hub_standalone: FACTORY_JOBS provisioning failed — "
                "hub cannot announce ready. Cause: %s. RestartSec will recover.",
                exc,
            )
            raise

        from factory.infrastructure.events.stream_setup import (
            ensure_observability_streams,
        )

        try:
            await ensure_observability_streams(_audio_js)
        except nats.errors.Error as exc:
            log.critical(
                "hub_standalone: factory-events/metrics provisioning failed — "
                "ingress cannot publish webhooks. Cause: %s. RestartSec will recover.",
                exc,
            )
            raise

        # Provision active-jobs KV bucket before announcing readiness.
        # Workers / adapters consulting the registry rely on the bucket
        # existing before they receive the hub-ready signal. ADR-079 S3.
        from factory.infrastructure.stores.jobs.active_jobs_kv import (
            ensure_active_jobs_kv,
        )

        try:
            await ensure_active_jobs_kv(_audio_js)
        except nats.errors.Error as exc:
            log.critical(
                "hub_standalone: active-jobs KV provisioning failed — "
                "registry unavailable; hub cannot announce ready. "
                "Cause: %s. RestartSec will recover.",
                exc,
            )
            raise

        from factory.infrastructure.jobs.dlq_router import DlqRouter

        _dlq_router = DlqRouter(nc, _audio_js)
        await _dlq_router.start()

        from factory.infrastructure.stores.jobs.active_jobs_kv import KvActiveJobsStore
        from factory.infrastructure.stores.jobs.active_jobs_refresher import (
            RegistryCoordinator,
        )

        _active_jobs_store = KvActiveJobsStore(_audio_js)
        await _active_jobs_store.connect()
        _active_jobs_coord = RegistryCoordinator(_active_jobs_store)
        _active_jobs_coord.start()
        hub._active_jobs_store = _active_jobs_store  # noqa: SLF001 — dashboard RPC (#1772)
        hub._active_jobs_coord = _active_jobs_coord  # noqa: SLF001

        # Publish each bot's watch_channels into factory-state KV before
        # announcing readiness so adapters see the value on first seed (SC6).
        _bots: list[tuple[str, str]] = [
            ("telegram", cfg.bot_id) for cfg, _ in tg_bot_auths
        ] + [("discord", cfg.bot_id) for cfg, _ in dc_bot_auths]
        try:
            await publish_watch_channels(_audio_js, stores.agent, _bots)
        except nats.errors.Error as exc:
            log.critical("hub: failed to publish watch_channels: %s", exc)
            raise

        try:
            await publish_bot_roster(_audio_js, stores.bot)
        except nats.errors.Error as exc:
            log.critical("hub: failed to publish bot roster: %s", exc)
            raise

        try:
            await publish_agent_roster(_audio_js, stores.agent)
        except nats.errors.Error as exc:
            log.critical("hub: failed to publish web agent roster: %s", exc)
            raise

        from factory.bootstrap.factory.dashboard_rpc import start_dashboard_rpc
        from factory.bootstrap.fleet_ingest import start_fleet_ingest
        from factory.bootstrap.fleet_reporter import start_fleet_reporter

        await start_fleet_ingest(hub, nc)
        fleet_reporter_task = await start_fleet_reporter(nc)
        await start_dashboard_rpc(hub, nc)

        await announce_hub_ready(nc)
        readiness_sub = await start_readiness_responder(nc, [hub.inbound_bus])

        health_server, health_port = create_health_server(hub, nc=nc)

        stop = _stop if _stop is not None else asyncio.Event()
        if _stop is None:
            setup_signal_handlers(stop)

        tasks = _create_hub_tasks(hub, health_server)
        if fleet_reporter_task is not None:
            tasks.append(fleet_reporter_task)

        active = _build_active_list(tg_bot_auths, dc_bot_auths)
        log.info(
            "Hub standalone started — NATS proxies: %s, health on :%d.",
            ", ".join(active) if active else "none",
            health_port,
        )

        await notify_startup(active, health_port)

        await _run_shutdown(
            tasks,
            stop,
            mint_failure_sub,
            hub,
            readiness_sub=readiness_sub,
            dispatchers=dispatchers,
            proxies=proxies,
            pm=pm,
            cli_nats_driver=cli_nats_driver,
            nats_llm_client=nats_llm_client,
        )
        # #1797 drives open()/close(); #1795 JobResult close trigger deferred
        # (no hub-side factory.job.*.result sub yet)
        await _active_jobs_coord.stop()
        await _dlq_router.stop()

    # Close NATS connection after stores context exits
    try:
        await nc.close()
        log.info("NATS connection closed.")
    except nats.errors.Error as exc:
        log.warning("Error closing NATS connection: %s", exc)

    release_lockfile()
    log.info("Hub standalone stopped.")
