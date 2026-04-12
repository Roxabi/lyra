"""Worker/process management helpers for CliPool — split from cli_pool.py (#293).

Contains _ProcessEntry, subprocess spawn/kill helpers, and the idle reaper.
CliPool (cli_pool.py) inherits from CliPoolWorkerMixin to preserve the public API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agent_config import ModelConfig
from .cli_protocol import _read_stderr_snippet, build_cmd

log = logging.getLogger(__name__)

# Explicit env allowlist — only forward safe vars to the claude subprocess.
_SAFE_ENV_KEYS = {
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
}


def _find_project_root() -> Path:
    """Locate the project root by searching for pyproject.toml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate project root (no pyproject.toml found)")


# cwd for the claude subprocess — lyra project root
_LYRA_ROOT = _find_project_root()


@dataclass
class _ProcessEntry:
    """A persistent CLI process for one pool."""

    proc: asyncio.subprocess.Process
    pool_id: str
    model_config: ModelConfig
    system_prompt: str = ""
    session_id: str | None = None
    resumed_from: str | None = None  # session_id passed to --resume at spawn
    # tmpfile for --system-prompt-file (cleaned on kill)
    prompt_file: str | None = None
    turn_count: int = 0
    last_activity: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _on_session_update: Callable[[str, str], None] | None = field(
        default=None, repr=False
    )

    def is_alive(self) -> bool:
        return self.proc.returncode is None

    def update_session_id(self, sid: str | None) -> None:
        """Set session_id and fire the persist callback if changed."""
        if sid and sid != self.session_id:
            self.session_id = sid
            if self._on_session_update is not None:
                try:
                    self._on_session_update(self.pool_id, sid)
                except Exception:
                    log.debug("[pool:%s] session update callback failed", self.pool_id)


class CliPoolWorkerMixin:
    """Base class providing spawn/kill worker methods for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _entries: dict[str, _ProcessEntry]
        _cwd_overrides: dict[str, Path]
        _resume_session_ids: dict[str, str]
        _default_timeout: int
        _kill_timeout: float
        _reaper_interval: int
        _read_buffer_bytes: int
        _idle_ttl: int
        _last_sweep_at: float | None
        _on_reap: Callable[[str, str], Coroutine[Any, Any, None]] | None

    def _build_cmd(
        self,
        model_config: ModelConfig,
        session_id: str | None = None,
        system_prompt: str = "",
    ) -> tuple[list[str], str | None]:
        return build_cmd(model_config, session_id, system_prompt)

    async def _spawn(
        self, pool_id: str, model_config: ModelConfig, system_prompt: str = ""
    ) -> _ProcessEntry | None:
        spawn_cwd = self._cwd_overrides.get(pool_id) or model_config.cwd or _LYRA_ROOT
        resume_session_id = self._resume_session_ids.pop(pool_id, None)
        cmd, prompt_file = self._build_cmd(
            model_config,
            session_id=resume_session_id,
            system_prompt=system_prompt,
        )
        log.info(
            "[pool:%s] spawning: backend=%s model=%s cwd=%s resume=%s",
            pool_id,
            model_config.backend,
            model_config.model,
            spawn_cwd,
            resume_session_id or "-",
        )
        log.debug("[pool:%s] cmd: %s", pool_id, " ".join(cmd))
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        env["HOME"] = str(Path.home())
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(spawn_cwd),
                limit=self._read_buffer_bytes,  # prevents LimitOverrunError
                env=env,
            )
        except Exception as exc:
            log.error("[pool:%s] failed to spawn: %s", pool_id, exc)
            if prompt_file:
                Path(prompt_file).unlink(missing_ok=True)
            return None

        # Early liveness check: if the process dies within 100ms (e.g. auth
        # failure, missing binary, bad flags), capture stderr and fail fast
        # instead of returning an entry that will produce "stdout EOF".
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass  # still alive — expected happy path
        else:
            # Process already exited
            stderr_snippet = await _read_stderr_snippet(proc)
            log.error(
                "[pool:%s] process exited immediately (rc=%d): %s",
                pool_id,
                proc.returncode or -1,
                stderr_snippet or "(no stderr)",
            )
            if prompt_file:
                Path(prompt_file).unlink(missing_ok=True)
            return None

        # Wire the session persist callback if the subclass provides it.
        _persist_fn = getattr(self, "_persist_cli_session", None)
        entry = _ProcessEntry(
            proc=proc,
            pool_id=pool_id,
            model_config=model_config,
            system_prompt=system_prompt,
            resumed_from=resume_session_id,
            prompt_file=prompt_file,
            _on_session_update=_persist_fn,
        )
        self._entries[pool_id] = entry
        log.info("[pool:%s] spawned (PID=%d)", pool_id, proc.pid)
        return entry

    def _maybe_preserve_session(
        self, pool_id: str, entry: _ProcessEntry, *, preserve_session: bool
    ) -> None:
        """Write or clear _resume_session_ids based on preserve_session.

        Shared by _kill and _sync_evict_entry — single definition of the
        preservation contract so both callers stay in sync automatically.

        When preserve_session=False (reset/clear/folder), any previously
        scheduled resume for this pool is discarded so the next spawn starts
        fresh.

        Note: the session file existence check was removed (#415) because
        stream-json mode does not flush .jsonl while the subprocess is alive,
        causing spurious resume failures after restart.
        """
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
        """Sync counterpart to _kill for use in synchronous eviction paths.

        Pops the entry and cwd_override immediately within a single synchronous
        frame (no event-loop yield). If preserve_session=True and entry.session_id
        is set, stores session_id in _resume_session_ids for one-shot pickup by
        the next _spawn().

        Does NOT terminate the process — once the entry is removed from _entries,
        the idle reaper's snapshot will not include this pool_id, so the orphaned
        process persists until natural idle-timeout or parent exit.
        """
        entry = self._entries.pop(pool_id, None)
        self._cwd_overrides.pop(pool_id, None)
        if entry is None:
            return
        self._maybe_preserve_session(pool_id, entry, preserve_session=preserve_session)

    async def _kill(self, pool_id: str, *, preserve_session: bool = True) -> None:
        entry = self._entries.pop(pool_id, None)
        self._cwd_overrides.pop(pool_id, None)
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
            except Exception as exc:
                log.warning("idle reaper error: %s", exc)
