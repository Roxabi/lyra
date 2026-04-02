"""Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from .agent_config import ModelConfig
from .cli_pool_worker import (
    _LYRA_ROOT,
    CliPoolWorkerMixin,
    _ProcessEntry,
)
from .cli_protocol import (
    _SESSION_ID_RE,
    CliProtocolOptions,
    CliResult,
    StreamingIterator,
    send_and_read,
    send_and_read_stream,
)

# Re-export private names that tests reference via `from lyra.core.cli_pool import …`
__all__ = [
    "CliPool",
    "CliResult",
    "_LYRA_ROOT",
    "_ProcessEntry",
]

log = logging.getLogger(__name__)


class CliPool(CliPoolWorkerMixin):
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
        session_store_dir: Path | None = None,
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
        # Persistent CLI session store — survives daemon restarts so --resume
        # uses the correct CLI session, not Lyra's internal session UUID.
        # Structure: {"by_pool": {pool_id: cli_sid}, "by_session": {lyra_sid: cli_sid}}
        self._cli_sessions_path = (
            session_store_dir or Path.home() / ".lyra"
        ) / "cli_sessions.json"
        self._cli_sessions: dict[str, dict[str, str]] = self._load_cli_sessions()
        # Dead-backend hit counter — incremented by pool_processor when a turn
        # completes suspiciously fast (< _MIN_EXPECTED_DURATION_MS).  Exposed
        # via the /health/detail endpoint so the monitor can catch silent failures.
        self._dead_backend_hits: int = 0
        # In-memory mapping of pool_id → current Lyra session UUID.
        # Updated by link_lyra_session() before each send, so the
        # _on_session_update callback can record {lyra_sid → cli_sid}.
        self._lyra_sessions: dict[str, str] = {}

    def _load_cli_sessions(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self._cli_sessions_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"by_pool": {}, "by_session": {}}
        # Migrate from old flat format (pool_id → cli_sid).
        if "by_pool" not in data:
            return {"by_pool": data, "by_session": {}}
        return data

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """Associate the current Lyra session UUID with *pool_id*.

        Called by the agent before each send so the persist callback can
        map ``lyra_session_id → cli_session_id`` (for reply-to-resume).
        """
        self._lyra_sessions[pool_id] = lyra_session_id

    def _persist_cli_session(self, pool_id: str, cli_session_id: str) -> None:
        """Persist pool_id → CLI session_id (and lyra_sid → CLI) to disk."""
        if not cli_session_id or not _SESSION_ID_RE.match(cli_session_id):
            return
        self._cli_sessions["by_pool"][pool_id] = cli_session_id
        lyra_sid = self._lyra_sessions.get(pool_id)
        if lyra_sid:
            self._cli_sessions["by_session"][lyra_sid] = cli_session_id
        try:
            self._cli_sessions_path.parent.mkdir(parents=True, exist_ok=True)
            self._cli_sessions_path.write_text(json.dumps(self._cli_sessions, indent=2))
        except OSError:
            log.warning("[pool:%s] failed to persist CLI session to disk", pool_id)

    async def start(self) -> None:
        """Start the idle reaper background task."""
        self._reaper_task = asyncio.create_task(self._idle_reaper())
        log.info("CliPool started (idle_ttl=%ds)", self._idle_ttl)

    async def drain(self, timeout: float = 60.0) -> None:
        """Wait for all in-flight turns to complete before stopping.

        A turn is considered in-flight when its ``_lock`` is held.  Idle
        processes (alive but waiting for the next message) are not counted —
        they are safe to kill immediately.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            inflight = [pid for pid, e in self._entries.items() if e._lock.locked()]
            if not inflight:
                return
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                log.warning(
                    "CliPool drain timeout — %d turn(s) still in-flight: %s",
                    len(inflight),
                    inflight,
                )
                return
            log.info(
                "CliPool draining — %d turn(s) in-flight, %.0fs remaining…",
                len(inflight),
                remaining,
            )
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        """Stop reaper and kill all processes."""
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        for pool_id in list(self._entries):
            await self._kill(pool_id)
        log.info("CliPool stopped")

    async def send(  # noqa: C901
        self,
        pool_id: str,
        message: str,
        model_config: ModelConfig,
        system_prompt: str = "",
        *,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
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
                        on_intermediate=on_intermediate,
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

    # How long to wait after a resumed spawn to detect a stale session.
    # The CLI exits within ~1ms when a session doesn't exist; 50ms gives
    # the asyncio child watcher plenty of time to set proc.returncode.
    _STALE_RESUME_CHECK_DELAY = 0.05

    async def send_streaming(  # noqa: C901
        self,
        pool_id: str,
        message: str,
        model_config: ModelConfig,
        system_prompt: str = "",
        *,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> StreamingIterator:
        """Send a message and return a streaming iterator for text_delta chunks.

        Locking model: acquire entry._lock → write stdin → release lock →
        return iterator.  The lock is released before the first chunk is
        yielded so that concurrent reset() calls do not deadlock.
        """
        for _attempt in range(2):  # at most one stale-resume retry
            entry = self._entries.get(pool_id)

            if entry is None or not entry.is_alive():
                entry = await self._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    raise RuntimeError("Failed to spawn Claude CLI process")
            elif entry.system_prompt != system_prompt:
                log.info(
                    "[pool:%s] system_prompt changed — respawning (streaming)",
                    pool_id,
                )
                await self._kill(pool_id, preserve_session=False)
                entry = await self._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    raise RuntimeError("Failed to respawn Claude CLI process")
            elif entry.model_config != model_config:
                log.info(
                    "[pool:%s] model_config mismatch — respawning (streaming)",
                    pool_id,
                )
                await self._kill(pool_id, preserve_session=False)
                entry = await self._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    raise RuntimeError("Failed to respawn Claude CLI process")

            _pool_id = pool_id

            async def _reset() -> None:
                await self.reset(_pool_id)

            # Lock: write stdin inside lock, release before returning the
            # read-only iterator.  This prevents concurrent stdin interleave
            # while allowing the iterator to be consumed without holding the lock.
            async with entry._lock:
                if not entry.is_alive():
                    raise RuntimeError("Process died before streaming send")
                iterator = await send_and_read_stream(
                    entry,
                    message,
                    pool_id,
                    pool_reset_fn=_reset,
                    default_timeout=self._default_timeout,
                    on_intermediate=on_intermediate,
                    opts=self._protocol_opts,
                )

            # Stale resume guard: if this process was spawned with --resume,
            # briefly yield to let the event loop process a potential child-exit
            # signal.  The CLI exits in ~1ms when the session doesn't exist.
            if _attempt == 0 and entry.resumed_from and entry.turn_count == 0:
                await asyncio.sleep(self._STALE_RESUME_CHECK_DELAY)
                if not entry.is_alive():
                    log.warning(
                        "[pool:%s] stale resume (session %s) — retrying"
                        " without --resume (streaming)",
                        pool_id,
                        entry.resumed_from,
                    )
                    await self._kill(pool_id, preserve_session=False)
                    continue

            entry.turn_count += 1
            entry.last_activity = time.time()
            return iterator

        raise RuntimeError("Failed after stale resume retry")

    def record_dead_backend_hit(self) -> None:
        """Increment the dead-backend counter (called by PoolProcessor)."""
        self._dead_backend_hits += 1

    @property
    def dead_backend_hits(self) -> int:
        """Number of suspiciously-fast responses since last reset."""
        return self._dead_backend_hits

    def reset_dead_backend_hits(self) -> None:
        """Reset the counter (called after a successful restart)."""
        self._dead_backend_hits = 0

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
        looks up the real Claude CLI session ID from the persistent store
        (``~/.lyra/cli_sessions.json``) and uses *that* for ``--resume``.

        Returns True if the resume was accepted, False if skipped (no persisted
        CLI session, invalid id, or process already on that session).
        """
        # Translate Lyra session → CLI session from the persistent store.
        # Try exact Lyra-session mapping first (reply-to-resume), then
        # fall back to latest CLI session for the pool (last-active-resume).
        cli_sid = self._cli_sessions.get("by_session", {}).get(
            session_id
        ) or self._cli_sessions.get("by_pool", {}).get(pool_id)
        if cli_sid is None:
            log.info(
                "[pool:%s] resume_and_reset: no persisted CLI session"
                " — starting fresh (lyra_session=%s)",
                pool_id,
                session_id,
            )
            return False
        if not _SESSION_ID_RE.match(cli_sid):
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
