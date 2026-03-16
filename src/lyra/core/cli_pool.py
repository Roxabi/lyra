"""Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .agent import ModelConfig

log = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[0-9a-f-]{8,64}$")


@dataclass
class CliResult:
    """Result from a CliPool.send() call.

    Exactly one of `result` or `error` will be set (not both).
    `warning` is set when the response was truncated (e.g. max_turns reached).
    `session_id` is always present on success.
    """

    result: str = ""
    session_id: str = ""
    error: str = ""
    warning: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


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
                result = await self._send_and_read(entry, message, on_intermediate)
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
            log.warning("[pool:%s] resume_and_reset: invalid session_id %r — skipping", pool_id, session_id)  # noqa: E501
            return
        if not self._session_file_exists(session_id):
            log.info("[pool:%s] resume_and_reset: session %r not on disk — skipping (Tier-2)", pool_id, session_id)  # noqa: E501
            return
        # is_idle verified by caller; race window is sub-millisecond.
        await self._kill(pool_id)
        self._resume_session_ids[pool_id] = session_id
        log.info("[pool:%s] resume_and_reset: will resume %s on next spawn", pool_id, session_id)  # noqa: E501

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

    async def _send_and_read(
        self,
        entry: _ProcessEntry,
        message: str,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> CliResult:
        proc = entry.proc
        if proc.stdin is None:
            return CliResult(error="Process stdin is None")

        payload = {
            "type": "user",
            "message": {"role": "user", "content": message},
            "session_id": entry.session_id or "",
            "parent_tool_use_id": None,
        }
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        try:
            await asyncio.wait_for(proc.stdin.drain(), timeout=10)
        except asyncio.TimeoutError:
            await self._kill(entry.pool_id)
            return CliResult(error="Timeout writing to subprocess stdin")

        return await self._read_until_result(entry, on_intermediate)

    async def _read_until_result(  # noqa: C901, PLR0915 — protocol dispatch: each JSON event type requires its own branch
        self,
        entry: _ProcessEntry,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> CliResult:
        proc = entry.proc
        if proc.stdout is None:
            return CliResult(error="Process stdout is None")

        idle_timeout = self._default_timeout  # resets on each event
        max_idle_retries = 3
        idle_retries = 0
        session_id: str | None = None
        result_parts: list[str] = []  # accumulate text across all assistant events
        assistant_turn_count = 0
        # Buffer current turn; previous turn sent as ⏳ intermediate
        pending_intermediate: str | None = None

        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=idle_timeout
                    )
                except asyncio.TimeoutError:
                    # No output for idle_timeout seconds — process may be stuck.
                    if not entry.is_alive():
                        log.warning(
                            "[pool:%s] process died during idle wait", entry.pool_id
                        )
                        return CliResult(error="Process terminated unexpectedly")
                    idle_retries += 1
                    if idle_retries >= max_idle_retries:
                        total_wait = idle_timeout * max_idle_retries
                        log.error(
                            "[pool:%s] Timeout: no output for %ds (%d retries)",
                            entry.pool_id,
                            total_wait,
                            max_idle_retries,
                        )
                        return CliResult(
                            error=f"Timeout: no output for {total_wait}s"
                        )
                    log.warning(
                        "[pool:%s] no output for %ds — alive, waiting (%d/%d)",
                        entry.pool_id,
                        idle_timeout,
                        idle_retries,
                        max_idle_retries,
                    )
                    continue

                if not raw:
                    log.warning("[pool:%s] stdout EOF (process died)", entry.pool_id)
                    return CliResult(error="Process terminated unexpectedly")

                idle_retries = 0  # got data — reset timeout counter
                line = raw.decode().strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("[pool:%s] non-JSON: %s", entry.pool_id, line[:100])
                    continue

                msg_type = data.get("type", "")

                if msg_type == "system" and data.get("subtype") == "init":
                    session_id = data.get("session_id", "")
                    model = data.get("model")
                    log.debug("[pool:%s] init: model=%s", entry.pool_id, model)
                if msg_type == "assistant":
                    blocks = data.get("message", {}).get("content", [])
                    texts = [b["text"] for b in blocks if b.get("type") == "text"]
                    result_parts.extend(texts)
                    assistant_turn_count += 1
                    if on_intermediate and texts and assistant_turn_count >= 2:
                        # Flush the *previous* buffered turn as ⏳ before
                        # buffering the current one.
                        if pending_intermediate is not None:
                            try:
                                await asyncio.wait_for(
                                    on_intermediate(pending_intermediate),
                                    timeout=5.0,
                                )
                            except asyncio.TimeoutError:
                                log.warning(
                                    "[pool:%s] on_intermediate callback timed"
                                    " out (>5s) — dropping",
                                    entry.pool_id,
                                )
                            except Exception:
                                log.warning(
                                    "[pool:%s] on_intermediate callback failed",
                                    entry.pool_id,
                                    exc_info=True,
                                )
                        pending_intermediate = "\n\n".join(texts)

                if msg_type == "result":
                    if not session_id:
                        session_id = data.get("session_id", "")
                    if session_id and entry.session_id != session_id:
                        entry.session_id = session_id
                    if pending_intermediate is not None:
                        result_text = pending_intermediate
                    else:
                        result_text = data.get("result") or "\n\n".join(result_parts)
                    log.info(
                        "[pool:%s] response: %d chars, %dms",
                        entry.pool_id,
                        len(result_text),
                        data.get("duration_ms", 0),
                    )

                    if data.get("is_error", False):
                        subtype = data.get("subtype", "")
                        if subtype == "error_max_turns" and result_text:
                            return CliResult(
                                result=result_text,
                                session_id=session_id or "",
                                warning="Response truncated (max turns reached)",
                            )
                        return CliResult(
                            error=result_text or "Unknown error from Claude"
                        )

                    return CliResult(result=result_text, session_id=session_id or "")

        except Exception as exc:
            log.exception("[pool:%s] read error: %s", entry.pool_id, exc)
            return CliResult(error=f"Read error: {type(exc).__name__}")

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
