"""Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from .agent_config import ModelConfig
from .cli_pool_worker import (
    _CLAUDE_PROJECTS,
    _LYRA_ROOT,
    _CliPoolWorker,
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


class CliPool(_CliPoolWorker):
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
        on_reap: Callable[
            [str, str], Coroutine[Any, Any, None]
        ]
        | None = None,
        *,
        reaper_interval: int = 60,
        kill_timeout: float = 5.0,
        read_buffer_bytes: int = 1024 * 1024,
        stdin_drain_timeout: float = 10.0,
        max_idle_retries: int = 3,
        intermediate_timeout: float = 5.0,
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

    async def start(self) -> None:
        """Start the idle reaper background task."""
        self._reaper_task = asyncio.create_task(self._idle_reaper())
        log.info("CliPool started (idle_ttl=%ds)", self._idle_ttl)

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

    async def send(
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
            await self._kill(pool_id)
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
                entry.turn_count += 1
                entry.last_activity = time.time()
                return result
            except Exception as exc:
                log.exception("[pool:%s] send failed: %s", pool_id, exc)
                await self._kill(pool_id)
                return CliResult(error=f"Send failed: {type(exc).__name__}")

    async def send_streaming(
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
            await self._kill(pool_id)
            entry = await self._spawn(pool_id, model_config, system_prompt)
            if entry is None:
                raise RuntimeError("Failed to respawn Claude CLI process")
        elif entry.model_config != model_config:
            log.info(
                "[pool:%s] model_config mismatch — respawning (streaming)",
                pool_id,
            )
            await self._kill(pool_id)
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
        entry.turn_count += 1
        entry.last_activity = time.time()
        return iterator

    def is_alive(self, pool_id: str) -> bool:
        """Return True if a live process exists for pool_id."""
        entry = self._entries.get(pool_id)
        return entry is not None and entry.is_alive()

    async def reset(self, pool_id: str) -> None:
        """Kill the process for this pool. Next send() spawns a fresh one."""
        await self._kill(pool_id)
        log.info("[pool:%s] reset", pool_id)

    async def switch_cwd(self, pool_id: str, cwd: Path) -> None:
        """Kill any existing process and store cwd override. Next send() respawns."""
        await self._kill(pool_id)  # _kill pops _cwd_overrides — set override after
        self._cwd_overrides[pool_id] = cwd
        log.info("[pool:%s] workspace switched to %s", pool_id, cwd)

    def _session_file_exists(self, session_id: str) -> bool:
        """Return True if a Claude session JSONL file exists for this session_id."""
        return any(_CLAUDE_PROJECTS.glob(f"*/{session_id}.jsonl"))

    async def resume_and_reset(self, pool_id: str, session_id: str) -> None:
        """Kill process; next _spawn() uses --resume <session_id> (one-shot).

        No-op on invalid session_id format or pruned session file (Tier-2).
        """
        if not _SESSION_ID_RE.match(session_id):
            log.warning(
                "[pool:%s] resume_and_reset: invalid session_id %r — skipping",
                pool_id,
                session_id,
            )  # noqa: E501
            return
        if not self._session_file_exists(session_id):
            log.info(
                "[pool:%s] resume_and_reset: session %r not on disk — skipping (Tier-2)",  # noqa: E501
                pool_id,
                session_id,
            )
            return
        # If the live process already holds this session, skip kill+respawn.
        entry = self._entries.get(pool_id)
        if entry is not None and entry.is_alive() and entry.session_id == session_id:
            log.debug(
                "[pool:%s] resume_and_reset: process already on session %s — no-op",
                pool_id,
                session_id,
            )
            return
        # is_idle verified by caller; race window is sub-millisecond.
        await self._kill(pool_id)
        self._resume_session_ids[pool_id] = session_id
        log.info(
            "[pool:%s] resume_and_reset: will resume %s on next spawn",
            pool_id,
            session_id,
        )  # noqa: E501
