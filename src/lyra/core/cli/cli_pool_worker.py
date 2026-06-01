"""Worker/process management helpers for CliPool — split from cli_pool.py (#293).

Contains subprocess spawn/kill helpers and the idle reaper.
_ProcessEntry lives in cli_pool_entry to avoid circular imports with
protocol.cli_non_streaming / protocol.cli_streaming (both of which import
cli_protocol_types).
CliPool (cli_pool.py) inherits from CliPoolWorkerMixin to preserve the public API.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .cli_pool_entry import _ProcessEntry
from .cli_pool_spawn import _LYRA_ROOT, _SAFE_ENV_KEYS, CliPoolSpawnMixin

if TYPE_CHECKING:
    from lyra.core.ports.audit_sink import AuditSink

# Re-export so existing `from .cli_pool_worker import _ProcessEntry` keeps working.
__all__ = ["_ProcessEntry", "CliPoolWorkerMixin", "_LYRA_ROOT", "_SAFE_ENV_KEYS"]

log = logging.getLogger(__name__)


class CliPoolWorkerMixin(CliPoolSpawnMixin):
    """Base class providing spawn/kill worker methods for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _entries: dict[str, _ProcessEntry]
        _cwd_overrides: dict[str, Path]
        _resume_session_ids: dict[str, str]
        _default_timeout: int
        _kill_timeout: float
        _reaper_interval: int
        _idle_ttl: int
        _last_sweep_at: float | None
        _on_reap: Callable[[str, str], Coroutine[Any, Any, None]] | None
        _audit_sink: AuditSink | None
        _audit_tasks: set[asyncio.Task[None]]

    def _maybe_preserve_session(
        self, pool_id: str, entry: _ProcessEntry, *, preserve_session: bool
    ) -> None:
        # Session file check removed (#415): stream-json doesn't flush .jsonl
        # while alive, causing spurious resume failures after restart.
        if preserve_session and entry.session_id:
            self._resume_session_ids[pool_id] = entry.session_id
            # Also persist to disk so the session survives daemon restarts.
            _persist = getattr(self, "_persist_cli_session", None)
            if _persist is not None:
                _persist(pool_id, entry.session_id)
            log.debug(
                "[pool:%s] preserving session %s for auto-resume",
                pool_id,
                entry.session_id,
            )
        elif not preserve_session:
            # Explicit reset (/clear, /folder) — discard any stale scheduled resume.
            self._resume_session_ids.pop(pool_id, None)
            log.debug(
                "[pool:%s] discarded stale resume session (explicit reset)",
                pool_id,
            )

    def _sync_evict_entry(self, pool_id: str, *, preserve_session: bool = True) -> None:
        """Sync eviction: pops entry; orphaned process idles out naturally."""
        entry = self._entries.pop(pool_id, None)
        self._cwd_overrides.pop(pool_id, None)
        if entry is None:
            return
        self._maybe_preserve_session(pool_id, entry, preserve_session=preserve_session)

    async def _kill(self, pool_id: str, *, preserve_session: bool = True) -> None:
        entry = self._entries.pop(pool_id, None)
        self._cwd_overrides.pop(pool_id, None)
        if not preserve_session:
            self._resume_session_ids.pop(pool_id, None)
        if entry is None:
            return
        self._maybe_preserve_session(pool_id, entry, preserve_session=preserve_session)
        if entry.is_alive():
            try:
                entry.proc.terminate()
                try:
                    await asyncio.wait_for(
                        entry.proc.wait(),
                        timeout=self._kill_timeout,
                    )
                except asyncio.TimeoutError:
                    entry.proc.kill()
                    await entry.proc.wait()
            except ProcessLookupError:
                pass
        if entry.prompt_file:
            Path(entry.prompt_file).unlink(missing_ok=True)
        log.debug("[pool:%s] killed", pool_id)

    async def _idle_reaper(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._reaper_interval)
                self._last_sweep_at = time.monotonic()
                now = time.time()
                snapshot = list(self._entries.items())  # snapshot before async _kill
                to_kill = [
                    (pool_id, entry)
                    for pool_id, entry in snapshot
                    if not entry.is_alive()
                    or (
                        (now - entry.last_activity) > self._idle_ttl
                        and not entry._lock.locked()
                    )
                ]
                for pool_id, entry in to_kill:
                    reason = "idle" if entry.is_alive() else "dead"
                    log.info("[pool:%s] reaping %s process", pool_id, reason)
                    await self._kill(pool_id)
                    if self._on_reap and reason == "idle":
                        try:
                            await self._on_reap(pool_id, reason)
                        except Exception:
                            log.error(
                                "[pool:%s] on_reap failed",
                                pool_id,
                                exc_info=True,
                            )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001  — DEBT:boundary-broad-catch# top-level boundary
                log.warning("idle reaper error: %s", exc)
