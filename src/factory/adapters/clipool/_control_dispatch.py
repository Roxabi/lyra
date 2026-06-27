"""Control-plane dispatch for CliPoolNatsWorker."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from factory.adapters.clipool._worker_helpers import _make_ack
from factory.core.cli.cli_pool import CliPool
from roxabi_contracts.cli.models import CliControlCmd

log = logging.getLogger(__name__)


async def dispatch_control(pool: CliPool, cmd: CliControlCmd) -> bytes:
    """Run a control op against *pool* and return a serialised CliControlAck."""
    if cmd.op == "reset":
        await pool.reset(cmd.pool_id)
        return _make_ack(cmd.pool_id, ok=True)

    if cmd.op == "resume_and_reset":
        if not cmd.cli_session_id:
            log.warning(
                "clipool_worker: resume_and_reset missing cli_session_id"
                " for pool_id=%r",
                cmd.pool_id,
            )
            return _make_ack(cmd.pool_id, ok=False)
        resumed = await pool.resume_direct(cmd.pool_id, cmd.cli_session_id)
        log.info(
            "clipool: resume %s pool=%s",
            "ok" if resumed else "cold-start",
            cmd.pool_id,
        )
        return _make_ack(cmd.pool_id, ok=True, resumed=resumed)

    if cmd.op == "switch_cwd":
        if not cmd.cwd:
            log.warning(
                "clipool_worker: switch_cwd missing cwd for pool_id=%r",
                cmd.pool_id,
            )
            return _make_ack(cmd.pool_id, ok=False)
        base_dir = Path(
            os.environ.get("FACTORY_CLAUDE_CWD", str(Path.home() / "projects"))
        ).resolve()
        try:
            resolved = Path(cmd.cwd).resolve()
            resolved.relative_to(base_dir)
        except ValueError:
            log.warning(
                "clipool_worker: switch_cwd path %r escapes base %r for pool_id=%r",
                cmd.cwd,
                str(base_dir),
                cmd.pool_id,
            )
            return _make_ack(cmd.pool_id, ok=False)
        await pool.switch_cwd(cmd.pool_id, resolved)
        return _make_ack(cmd.pool_id, ok=True)

    log.warning(
        "clipool_worker: unknown control op %r for pool_id=%r",
        cmd.op,
        cmd.pool_id,
    )
    return _make_ack(cmd.pool_id, ok=False)