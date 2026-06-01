"""Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.stores import TurnStoreProtocol

from lyra.core.ports.audit_sink import AuditSink

from .cli_pool_lifecycle import CliPoolLifecycleMixin
from .cli_pool_send import CliPoolDeps, CliPoolSendMixin
from .cli_pool_session import CliPoolSessionMixin
from .cli_pool_streaming import CliPoolStreamingMixin
from .cli_pool_worker import (
    _LYRA_ROOT,
    CliPoolWorkerMixin,
    _ProcessEntry,
)
from .protocol.cli_protocol import (
    SESSION_ID_RE,
    CliProtocolOptions,
    CliResult,
)

# Re-export private names that tests reference via
# `from lyra.core.cli.cli_pool import …`
__all__ = [
    "AuditSink",
    "CliPool",
    "CliPoolDeps",
    "CliResult",
    "_LYRA_ROOT",
    "_ProcessEntry",
]

log = logging.getLogger(__name__)


class CliPool(  # noqa: E501 — DEBT:lint-residual
    CliPoolLifecycleMixin,
    CliPoolStreamingMixin,
    CliPoolSessionMixin,
    CliPoolSendMixin,
    CliPoolWorkerMixin,
):
    """Pool of persistent Claude CLI processes (one per pool_id).

    Usage::

        pool = CliPool(idle_ttl=1200)
        await pool.start()

        result = await pool.send(pool_id, message, model_config)
        await pool.reset(pool_id)

        await pool.stop()
    """

    def __init__(self, deps: CliPoolDeps | None = None) -> None:
        d = deps or CliPoolDeps()
        self._idle_ttl = d.idle_ttl
        self._default_timeout = d.default_timeout
        self._on_reap = d.on_reap
        self._reaper_interval = d.reaper_interval
        self._kill_timeout = d.kill_timeout
        self._read_buffer_bytes = d.read_buffer_bytes
        self._protocol_opts = CliProtocolOptions(
            stdin_drain_timeout=d.stdin_drain_timeout,
            max_idle_retries=d.max_idle_retries,
            intermediate_timeout=d.intermediate_timeout,
        )
        self._entries: dict[str, _ProcessEntry] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        self._cwd_overrides: dict[str, Path] = {}
        self._resume_session_ids: dict[str, str] = {}
        self._last_sweep_at: float | None = None
        # TurnStore — wired after construction via set_turn_store().
        # Stores CLI session IDs in pool_sessions so --resume survives restarts.
        self._turn_store: TurnStoreProtocol | None = None
        # In-memory mapping of pool_id → current Lyra session UUID.
        # Updated by link_lyra_session() before each send, so the
        # _on_session_update callback can record {lyra_sid → cli_sid}.
        self._lyra_sessions: dict[str, str] = {}
        self._audit_sink: AuditSink | None = d.audit_sink
        # Anchors fire-and-forget audit emit tasks so GC cannot collect them
        # before completion. Done-callback removes each task on completion.
        self._audit_tasks: set[asyncio.Task[None]] = set()

    def is_alive(self, pool_id: str) -> bool:
        """Return True if a live process exists for pool_id."""
        entry = self._entries.get(pool_id)
        return entry is not None and entry.is_alive()

    def get_active_pool_ids(self) -> list[str]:
        """Return pool IDs with a currently running subprocess."""
        return [pid for pid, entry in self._entries.items() if entry.is_alive()]

    async def reset(self, pool_id: str) -> None:
        """Kill the process for this pool. Next send() spawns a fresh one."""
        await self._kill(pool_id, preserve_session=False)
        log.info("[pool:%s] reset", pool_id)

    async def switch_cwd(self, pool_id: str, cwd: Path) -> None:
        """Kill any existing process and store cwd override. Next send() respawns."""
        # _kill pops _cwd_overrides — set override after
        await self._kill(pool_id, preserve_session=False)
        self._cwd_overrides[pool_id] = cwd
        log.info("[pool:%s] workspace switched to %s", pool_id, cwd)

    async def resume_direct(self, pool_id: str, cli_session_id: str) -> bool:
        """Set up --resume using a pre-resolved cli_session_id (no TurnStore lookup).

        Called by CliPoolNatsWorker which receives cli_session_id directly from hub.
        Returns True if resume was set up, False if no-op (already on that session
        or invalid id).
        """
        if not cli_session_id or not SESSION_ID_RE.match(cli_session_id):
            log.warning(
                "[pool:%s] resume_direct: invalid cli_session_id %r",
                pool_id,
                cli_session_id,
            )
            return False
        entry = self._entries.get(pool_id)
        already_on_session = (
            entry is not None
            and entry.is_alive()
            and entry.session_id == cli_session_id
        )
        if already_on_session:
            log.info(
                "[pool:%s] resume_direct: already on session %s — no-op",
                pool_id,
                cli_session_id,
            )
            return True
        await self._kill(pool_id, preserve_session=False)
        self._resume_session_ids[pool_id] = cli_session_id
        log.info(
            "[pool:%s] resume_direct: will resume CLI session %s on next spawn",
            pool_id,
            cli_session_id,
        )
        return True

    async def resume_and_reset(self, pool_id: str, session_id: str) -> bool:
        """Kill process; next _spawn() uses --resume <cli_session_id> (one-shot).

        *session_id* is the Lyra-internal UUID from the turn store.  This method
        looks up the real Claude CLI session ID from TurnStore (pool_sessions table)
        and uses *that* for ``--resume``.

        Returns True if the resume was accepted, False if skipped (no persisted
        CLI session, invalid id, or process already on that session).
        """
        # Translate Lyra session → CLI session from TurnStore.
        # Try exact session lookup first (reply-to-resume), then fall back to
        # the most recent active session for the pool (last-active-resume).
        cli_sid: str | None = None
        if self._turn_store is not None:
            cli_sid = await self._turn_store.get_cli_session(session_id)
            if not cli_sid:
                cli_sid = await self._turn_store.get_cli_session_by_pool(pool_id)
        if cli_sid is None:
            log.info(
                "[pool:%s] resume_and_reset: no persisted CLI session"
                " — starting fresh (lyra_session=%s)",
                pool_id,
                session_id,
            )
            return False
        if not SESSION_ID_RE.match(cli_sid):
            log.warning(
                "[pool:%s] resume_and_reset: invalid persisted CLI session %r"
                " — skipping",
                pool_id,
                cli_sid,
            )
            return False
        # If the live process already holds this CLI session, skip kill+respawn.
        entry = self._entries.get(pool_id)
        if entry is not None and entry.is_alive() and entry.session_id == cli_sid:
            log.info(
                "[pool:%s] resume_and_reset: process already on CLI session %s — no-op",
                pool_id,
                cli_sid,
            )
            return True
        # is_idle verified by caller; race window is sub-millisecond.
        await self._kill(pool_id, preserve_session=False)
        self._resume_session_ids[pool_id] = cli_sid
        log.info(
            "[pool:%s] resume_and_reset: will resume CLI session %s on next"
            " spawn (lyra_session=%s)",
            pool_id,
            cli_sid,
            session_id,
        )
        return True
