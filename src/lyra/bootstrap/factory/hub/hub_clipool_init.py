"""Initialize clipool bundle — LlmClient, CliPool, CliPoolNatsWorker."""

from __future__ import annotations

import logging

from nats.aio.client import Client as NATS

from lyra.bootstrap.bootstrap_stores import StoreBundle
from lyra.bootstrap.factory.config import _load_cli_pool_config
from lyra.bootstrap.factory.hub.hub_llm_client import build_llm_client
from lyra.bootstrap.types import CliPoolBundle
from lyra.core.cli.cli_pool import CliPool, CliPoolDeps
from lyra.infrastructure.audit import JetStreamAuditSink

log = logging.getLogger(__name__)


async def _init_clipool(
    nc: NATS,
    raw_config: dict,
    stores: StoreBundle,
) -> CliPoolBundle:
    """Build LlmClient (clipool), CliPool, CliPoolNatsWorker."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    cli_pool_cfg = _load_cli_pool_config(raw_config)
    audit_sink = JetStreamAuditSink()
    await audit_sink.provision(nc)

    cli_nats_driver = await build_llm_client(nc)
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
            audit_sink=audit_sink,
        )
    )
    await cli_pool.start()
    cli_pool.set_turn_store(stores.turn)
    worker = CliPoolNatsWorker(cli_pool, timeout=cli_pool_cfg.default_timeout)

    return CliPoolBundle(
        cli_pool=cli_pool,
        cli_nats_driver=cli_nats_driver,
        worker=worker,
        audit_sink=audit_sink,
    )
