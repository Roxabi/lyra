"""Build CLI pool for hub — starts CliPool if any agent uses claude-cli backend."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from factory.bootstrap.factory.config import _load_cli_pool_config
from factory.core.agent import Agent
from factory.core.cli.cli_pool import CliPool, CliPoolDeps

if TYPE_CHECKING:
    from factory.core.ports.audit_sink import AuditSink

log = logging.getLogger(__name__)


async def build_cli_pool(
    raw_config: dict,
    agent_configs: dict[str, Agent],
    *,
    audit_sink: AuditSink | None = None,
) -> CliPool | None:
    """Build and start a CliPool if any agent uses the claude-cli backend."""
    cli_pool_cfg = _load_cli_pool_config(raw_config)
    for cfg in agent_configs.values():
        if cfg.llm_config.backend == "claude-cli":
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
            return cli_pool
    return None
