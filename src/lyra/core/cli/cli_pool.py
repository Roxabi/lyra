"""Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lyra.infrastructure.stores.turn_store import TurnStore

from ..agent.agent_config import ModelConfig
from .audit_sink import AuditSink
from .cli_pool_lifecycle import CliPoolLifecycleMixin
from .cli_pool_session import CliPoolSessionMixin
from .cli_pool_streaming import CliPoolStreamingMixin
from .cli_pool_worker import (
    _LYRA_ROOT,
    CliPoolWorkerMixin,
    _ProcessEntry,
)
from .cli_protocol import (
    SESSION_ID_RE,
    CliProtocolOptions,
    CliResult,
    send_and_read,
)

# Re-export private names that tests reference via
# `from lyra.core.cli.cli_pool import …`
__all__ = [
    "AuditSink",
    "CliPool",
    "CliResult",
    "_LYRA_ROOT",
    "_ProcessEntry",
]

log = logging.getLogger(__name__)


class CliPool(  # noqa: E501
    CliPoolLifecycleMixin,
    CliPoolStreamingMixin,
    CliPoolSessionMixin,
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

    def __init__(  # noqa: PLR0913
        self,
        idle_ttl: int = 1200,
        default_timeout: int = 1200,  # 20 min × 3 retries = 60 min max idle
        on_reap: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
        *,
        reaper_interval: int = 60,
        kill_timeout: float = 5.0,
        read_buffer_bytes: int = 1024 * 1024,
        stdin_drain_timeout: float = 10.0,
        max_idle_retries: int = 3,
        intermediate_timeout: float = 5.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._idle_ttl = idle_ttl
        self._default_timeout = default_timeout
        self._on_reap = on_reap
        self._reaper_interval = reaper_interval
        self._kill_timeout = kill_timeout
        self._read_buffer_bytes = read_buffer_bytes
        self._protocol_opts = CliProtocolOptions(
            stdin_drain_timeout=stdin_drain_timeout,
            max_idle_retries=max_idle_retries,
            intermediate_timeout=intermediate_timeout,
        )
        self._entries: dict[str, _ProcessEntry] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        self._cwd_overrides: dict[str, Path] = {}
        self._resume_session_ids: dict[str, str] = {}
        self._last_sweep_at: float | None = None
        # TurnStore — wired after construction via set_turn_store().
        # Stores CLI session IDs in pool_sessions so --resume survives restarts.
        self._turn_store: TurnStore | None = None
        # In-memory mapping of pool_id → current Lyra session UUID.
        # Updated by link_lyra_session() before each send, so the
        # _on_session_update callback can record {lyra_sid → cli_sid}.
        self._lyra_sessions: dict[str, str] = {}
        self._audit_sink: AuditSink | None = audit_sink
        # Anchors fire-and-forget audit emit tasks so GC cannot collect them
        # before completion. Done-callback removes each task on completion.
        self._audit_tasks: set[asyncio.Task[None]] = set()

    async def send(  # noqa: C901
        self,
        pool_id: str,
        message: str,
        model_config: ModelConfig,
        system_prompt: str = "",
    ) -> CliResult:
        """Send a message to the persistent process for this pool.

        Spawns a new process if needed.
        Returns a CliResult. Check result.ok to distinguish success from error.

        Locking model:
          - pool.lock (hub layer): serialises all messages for one user session.
          - entry._lock (cli_pool layer): serialises stdin/stdout access to one
            process. Currently redundant for single-agent use (pool.lock is held
            by the hub when send() is called), but required if multiple agents
            ever share a CliPool entry keyed by the same pool_id.
        """
        for _attempt in range(2):  # at most one stale-resume retry
            entry = self._entries.get(pool_id)

            if entry is None or not entry.is_alive():
                entry = await self._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    return CliResult(error="Failed to spawn Claude CLI process")
            elif entry.system_prompt != system_prompt:
                log.info(
                    "[pool:%s] system_prompt changed — respawning process",
                    pool_id,
                )
                await self._kill(pool_id, preserve_session=False)
                entry = await self._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    return CliResult(error="Failed to respawn Claude CLI process")
            elif entry.model_config != model_config:
                log.warning(
                    "[pool:%s] model_config mismatch — ignoring new config"
                    " (restart pool to apply). existing=%r requested=%r",
                    pool_id,
                    entry.model_config,
                    model_config,
                )

            # Re-check liveness inside lock (reaper may have killed
            # between check and acquire)
            async with entry._lock:
                if not entry.is_alive():
                    return CliResult(error="Process died before send")
                try:
                    result = await send_and_read(
                        entry,
                        message,
                        pool_id,
                        default_timeout=self._default_timeout,
                        opts=self._protocol_opts,
                    )
                    if not result.ok and (
                        "Timeout" in result.error or "terminated" in result.error
                    ):
                        await self._kill(pool_id)
                        return result
                    # Stale resume: CLI rejected a non-existent session.
                    # Kill and retry — _resume_session_ids was already consumed
                    # by _spawn(), so the retry spawns a fresh session.
                    if (
                        not result.ok
                        and _attempt == 0
                        and entry.resumed_from
                        # pre-increment: turn_count not yet bumped for this turn
                        and entry.turn_count == 0
                        and "No conversation found" in result.error
                    ):
                        log.warning(
                            "[pool:%s] stale resume (session %s) — retrying"
                            " without --resume",
                            pool_id,
                            entry.resumed_from,
                        )
                        await self._kill(pool_id, preserve_session=False)
                        continue
                    entry.turn_count += 1
                    entry.last_activity = time.time()
                    return result
                except Exception as exc:
                    log.exception("[pool:%s] send failed: %s", pool_id, exc)
                    await self._kill(pool_id)
                    return CliResult(error=f"Send failed: {type(exc).__name__}")

        return CliResult(error="Failed after stale resume retry")

    async def drain_audit_tasks(self, timeout: float = 5.0) -> None:
        """Flush in-flight audit emit tasks before shutdown."""
        if self._audit_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._audit_tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "drain_audit_tasks: timed out after %.1fs"
                    " with %d task(s) remaining",
                    timeout,
                    len(self._audit_tasks),
                )

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
