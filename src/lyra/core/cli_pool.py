"""Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .agent import ModelConfig
from .cli_protocol import _SESSION_ID_RE, CliResult, send_and_read

log = logging.getLogger(__name__)

# Explicit env allowlist — never forward secrets to the claude subprocess
_SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
}


def _find_project_root() -> Path:
    """Locate the project root by searching for pyproject.toml."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("Could not locate project root (no pyproject.toml found)")


# cwd for the claude subprocess — lyra project root
_LYRA_ROOT = _find_project_root()

# Claude CLI session files live at ~/.claude/projects/<cwd-slug>/<session_id>.jsonl
_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


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


class CliPool:
    """Pool of persistent Claude CLI processes (one per pool_id).

    Usage::

        pool = CliPool(idle_ttl=1200)
        await pool.start()

        result = await pool.send(pool_id, message, model_config)
        await pool.reset(pool_id)

        await pool.stop()
    """

    def __init__(self, idle_ttl: int = 1200, default_timeout: int = 300) -> None:
        self._idle_ttl = idle_ttl
        self._default_timeout = default_timeout
        self._entries: dict[str, _ProcessEntry] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        self._cwd_overrides: dict[str, Path] = {}
        self._resume_session_ids: dict[str, str] = {}

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
                )
                if not result.ok and "Timeout" in result.error:
                    await self._kill(pool_id)
                    return result
                entry.turn_count += 1
                entry.last_activity = time.time()
                return result
            except Exception as exc:
                log.exception("[pool:%s] send failed: %s", pool_id, exc)
                await self._kill(pool_id)
                return CliResult(error=f"Send failed: {type(exc).__name__}")

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

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

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
            "--max-turns",
            str(model_config.max_turns),
        ]
        if model_config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        if model_config.tools:
            cmd.extend(["--allowedTools", ",".join(model_config.tools)])
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    async def _spawn(
        self, pool_id: str, model_config: ModelConfig, system_prompt: str = ""
    ) -> _ProcessEntry | None:
        spawn_cwd = self._cwd_overrides.get(pool_id) or model_config.cwd or _LYRA_ROOT
        resume_session_id = self._resume_session_ids.pop(pool_id, None)
        cmd = self._build_cmd(
            model_config,
            session_id=resume_session_id,
            system_prompt=system_prompt,
        )
        log.info(
            "[pool:%s] spawning: backend=%s model=%s cwd=%s",
            pool_id,
            model_config.backend,
            model_config.model,
            spawn_cwd,
        )
        log.debug("[pool:%s] cmd: %s", pool_id, " ".join(cmd))
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(spawn_cwd),
                limit=1024 * 1024,  # 1MB — prevents LimitOverrunError
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
        self._entries[pool_id] = entry
        log.info("[pool:%s] spawned (PID=%d)", pool_id, proc.pid)
        return entry

    async def _kill(self, pool_id: str) -> None:
        entry = self._entries.pop(pool_id, None)
        self._cwd_overrides.pop(pool_id, None)
        if entry is None:
            return
        if entry.is_alive():
            try:
                entry.proc.terminate()
                try:
                    await asyncio.wait_for(entry.proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    entry.proc.kill()
                    await entry.proc.wait()
            except ProcessLookupError:
                pass
        log.debug("[pool:%s] killed", pool_id)

    async def _idle_reaper(self) -> None:
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                snapshot = list(self._entries.items())  # snapshot before async _kill
                to_kill = [
                    (pool_id, entry)
                    for pool_id, entry in snapshot
                    if not entry.is_alive()
                    or (now - entry.last_activity) > self._idle_ttl
                ]
                for pool_id, entry in to_kill:
                    reason = "idle" if entry.is_alive() else "dead"
                    log.info("[pool:%s] reaping %s process", pool_id, reason)
                    await self._kill(pool_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("idle reaper error: %s", exc)
