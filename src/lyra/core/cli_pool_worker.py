"""Worker/process management helpers for CliPool — split from cli_pool.py (#293).

Contains _ProcessEntry, subprocess spawn/kill helpers, and the idle reaper.
CliPool (cli_pool.py) inherits from CliPoolWorkerMixin to preserve the public API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .agent_config import ModelConfig

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
    turn_count: int = 0
    last_activity: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_alive(self) -> bool:
        return self.proc.returncode is None


class CliPoolWorkerMixin:
    """Base class providing spawn/kill worker methods for CliPool.

    Subclasses must initialise:
    - ``self._entries: dict[str, _ProcessEntry]``
    - ``self._cwd_overrides: dict[str, Path]``
    - ``self._resume_session_ids: dict[str, str]``
    - ``self._default_timeout: int``
    - ``self._kill_timeout: float``
    - ``self._reaper_interval: int``
    - ``self._read_buffer_bytes: int``
    """

    def _build_cmd(
        self,
        model_config: ModelConfig,
        session_id: str | None = None,
        system_prompt: str = "",
    ) -> list[str]:
        cmd = [
            "claude",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model_config.model,
            # max_turns=None means unlimited — omit the flag, let claude CLI decide.
            # max_turns=0 is treated as None (DB sentinel); any positive int is passed.
            *(
                [
                    "--max-turns",
                    str(model_config.max_turns),
                ]
                if model_config.max_turns
                else []
            ),
        ]
        if model_config.streaming:
            cmd.append("--include-partial-messages")
        if model_config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model_config.tools:
            cmd.extend(["--allowedTools", ",".join(model_config.tools)])
        # H-10: --system-prompt exposes the value in /proc/<pid>/cmdline.
        # A proper fix requires Claude CLI to support --system-prompt-file or
        # stdin-based system prompt delivery.
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    async def _spawn(
        self, pool_id: str, model_config: ModelConfig, system_prompt: str = ""
    ) -> _ProcessEntry | None:
        spawn_cwd = (
            self._cwd_overrides.get(pool_id)  # type: ignore[attr-defined]
            or model_config.cwd
            or _LYRA_ROOT
        )
        resume_session_id = self._resume_session_ids.pop(pool_id, None)  # type: ignore[attr-defined]
        cmd = self._build_cmd(
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
        # Redact system prompt from debug log to avoid log-file exposure
        _redacted = [
            "<redacted>" if i > 0 and cmd[i - 1] == "--system-prompt" else c
            for i, c in enumerate(cmd)
        ]
        log.debug("[pool:%s] cmd: %s", pool_id, " ".join(_redacted))
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        env["HOME"] = str(Path.home())
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(spawn_cwd),
                limit=self._read_buffer_bytes,  # type: ignore[attr-defined]  # prevents LimitOverrunError
                env=env,
            )
        except Exception as exc:
            log.error("[pool:%s] failed to spawn: %s", pool_id, exc)
            return None

        entry = _ProcessEntry(
            proc=proc,
            pool_id=pool_id,
            model_config=model_config,
            system_prompt=system_prompt,
        )
        self._entries[pool_id] = entry  # type: ignore[attr-defined]
        log.info("[pool:%s] spawned (PID=%d)", pool_id, proc.pid)
        return entry

    def _maybe_preserve_session(
        self, pool_id: str, entry: _ProcessEntry, *, preserve_session: bool
    ) -> None:
        """Write session_id to _resume_session_ids if both conditions hold.

        Shared by _kill and _sync_evict_entry — single definition of the
        preservation contract so both callers stay in sync automatically.

        Note: the session file existence check was removed (#415) because
        stream-json mode does not flush .jsonl while the subprocess is alive,
        causing spurious resume failures after restart.
        """
        if preserve_session and entry.session_id:
            self._resume_session_ids[pool_id] = entry.session_id  # type: ignore[attr-defined]
            log.debug(
                "[pool:%s] preserving session %s for auto-resume",
                pool_id,
                entry.session_id,
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
        entry = self._entries.pop(pool_id, None)  # type: ignore[attr-defined]
        self._cwd_overrides.pop(pool_id, None)  # type: ignore[attr-defined]
        if entry is None:
            return
        self._maybe_preserve_session(pool_id, entry, preserve_session=preserve_session)

    async def _kill(self, pool_id: str, *, preserve_session: bool = True) -> None:
        entry = self._entries.pop(pool_id, None)  # type: ignore[attr-defined]
        self._cwd_overrides.pop(pool_id, None)  # type: ignore[attr-defined]
        if entry is None:
            return
        self._maybe_preserve_session(pool_id, entry, preserve_session=preserve_session)
        if entry.is_alive():
            try:
                entry.proc.terminate()
                try:
                    await asyncio.wait_for(
                        entry.proc.wait(),
                        timeout=self._kill_timeout,  # type: ignore[attr-defined]
                    )
                except asyncio.TimeoutError:
                    entry.proc.kill()
                    await entry.proc.wait()
            except ProcessLookupError:
                pass
        log.debug("[pool:%s] killed", pool_id)

    async def _idle_reaper(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._reaper_interval)  # type: ignore[attr-defined]
                self._last_sweep_at = time.monotonic()  # type: ignore[attr-defined]
                now = time.time()
                snapshot = list(self._entries.items())  # type: ignore[attr-defined]  # snapshot before async _kill
                to_kill = [
                    (pool_id, entry)
                    for pool_id, entry in snapshot
                    if not entry.is_alive()
                    or (
                        (now - entry.last_activity) > self._idle_ttl  # type: ignore[attr-defined]
                        and not entry._lock.locked()
                    )
                ]
                for pool_id, entry in to_kill:
                    reason = "idle" if entry.is_alive() else "dead"
                    log.info("[pool:%s] reaping %s process", pool_id, reason)
                    await self._kill(pool_id)
                    # Fire-and-forget notification for idle evictions
                    if self._on_reap and reason == "idle":  # type: ignore[attr-defined]
                        _t = asyncio.create_task(
                            self._on_reap(pool_id, reason)  # type: ignore[attr-defined]
                        )
                        _t.add_done_callback(
                            lambda t, pid=pool_id: (
                                log.warning(
                                    "[pool:%s] on_reap failed: %s",
                                    pid,
                                    t.exception(),
                                )
                                if not t.cancelled() and t.exception()
                                else None
                            )
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("idle reaper error: %s", exc)
