"""Bootstrap standalone CliPool NATS worker."""

from __future__ import annotations

import logging
import os
import sys

from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker
from lyra.bootstrap.factory.config import _load_cli_pool_config
from lyra.core.cli.cli_pool import CliPool
from roxabi_nats.connect import scrub_nats_url

log = logging.getLogger(__name__)


async def _bootstrap_clipool_standalone(raw_config: dict) -> None:
    """Wire a standalone CliPoolNatsWorker connected to NATS."""
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL is required for standalone clipool mode.")

    cli_pool_cfg = _load_cli_pool_config(raw_config)

    log.info("clipool: will connect to NATS at %s", scrub_nats_url(nats_url))

    cli_pool = CliPool(
        idle_ttl=cli_pool_cfg.idle_ttl,
        default_timeout=cli_pool_cfg.default_timeout,
        reaper_interval=cli_pool_cfg.reaper_interval,
        kill_timeout=cli_pool_cfg.kill_timeout,
        read_buffer_bytes=cli_pool_cfg.read_buffer_bytes,
        stdin_drain_timeout=cli_pool_cfg.stdin_drain_timeout,
        max_idle_retries=cli_pool_cfg.max_idle_retries,
        intermediate_timeout=cli_pool_cfg.intermediate_timeout,
    )
    await cli_pool.start()

    worker = CliPoolNatsWorker(cli_pool, timeout=cli_pool_cfg.default_timeout)
    log.info("clipool: starting CliPoolNatsWorker on lyra.clipool.cmd")
    await worker.run(nats_url)
