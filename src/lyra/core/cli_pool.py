"""
Persistent Claude CLI process pool for Lyra agents.

One long-running `claude --input-format stream-json` process per pool_id.
Sends messages via stdin NDJSON, reads responses via stdout NDJSON.
Avoids the ~12s Node.js startup overhead on every message after the first.

Adapted from 2ndBrain/telegram_bot/core/claude_pool.py.
Key differences vs ClaudePool:
- Keyed by pool_id: str (not chat_id: int)
- Model config comes from ModelConfig (per-agent TOML), not per-send call
- No session persistence (added later when memory layer is ready)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import ModelConfig

log = logging.getLogger(__name__)


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
    ) -> dict[str, Any]:
        """Send a message to the persistent process for this pool.

        Spawns a new process if needed.
        Returns dict with 'result' and 'session_id', or 'error' key.
        """
        entry = self._entries.get(pool_id)

        if entry is None or not entry.is_alive():
            entry = await self._spawn(pool_id, model_config)
            if entry is None:
                return {"error": "Failed to spawn Claude CLI process"}
        elif entry.model_config != model_config:
            log.warning(
                "[pool:%s] model_config mismatch — ignoring new config"
                " (restart pool to apply)",
                pool_id,
            )

        async with entry._lock:
            if not entry.is_alive():
                return {"error": "Process died before send"}
            try:
                result = await self._send_and_read(entry, message)
                if "error" in result and "Timeout" in result.get("error", ""):
                    await self._kill(pool_id)
                    return result
                entry.turn_count += 1
                entry.last_activity = time.time()
                return result
            except Exception as exc:
                log.exception("[pool:%s] send failed: %s", pool_id, exc)
                await self._kill(pool_id)
                return {"error": f"Send failed: {type(exc).__name__}"}

    async def reset(self, pool_id: str) -> None:
        """Kill the process for this pool. Next send() spawns a fresh one."""
        await self._kill(pool_id)
        log.info("[pool:%s] reset", pool_id)

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _build_cmd(
        self, model_config: ModelConfig, session_id: str | None = None
    ) -> list[str]:
        cmd = [
            "claude",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--model", model_config.model,
            "--max-turns", str(model_config.max_turns),
        ]
        if model_config.tools:
            cmd.extend(["--allowedTools", ",".join(model_config.tools)])
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    async def _spawn(
        self, pool_id: str, model_config: ModelConfig
    ) -> _ProcessEntry | None:
        cmd = self._build_cmd(model_config)
        log.info(
            "[pool:%s] spawning: backend=%s model=%s",
            pool_id, model_config.backend, model_config.model,
        )
        log.debug("[pool:%s] cmd: %s", pool_id, " ".join(cmd))

        # Strip CLAUDECODE env var to allow nested Claude CLI sessions
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_LYRA_ROOT),
                limit=1024 * 1024,  # 1MB — prevents LimitOverrunError
                env=env,
            )
        except Exception as exc:
            log.error("[pool:%s] failed to spawn: %s", pool_id, exc)
            return None

        entry = _ProcessEntry(proc=proc, pool_id=pool_id, model_config=model_config)
        self._entries[pool_id] = entry
        log.info("[pool:%s] spawned (PID=%d)", pool_id, proc.pid)
        return entry

    async def _send_and_read(
        self, entry: _ProcessEntry, message: str
    ) -> dict[str, Any]:
        proc = entry.proc
        if proc.stdin is None:
            return {"error": "Process stdin is None"}

        payload = {
            "type": "user",
            "message": {"role": "user", "content": message},
            "session_id": entry.session_id or "",
            "parent_tool_use_id": None,
        }
        proc.stdin.write((json.dumps(payload) + "\n").encode())
        await proc.stdin.drain()

        return await self._read_until_result(entry)

    async def _read_until_result(self, entry: _ProcessEntry) -> dict[str, Any]:
        proc = entry.proc
        if proc.stdout is None:
            return {"error": "Process stdout is None"}

        timeout = self._default_timeout
        session_id: str | None = None
        result_text = ""

        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    log.warning(
                        "[pool:%s] deadline exceeded (%ds)", entry.pool_id, timeout
                    )
                    return {"error": f"Timeout after {timeout}s"}

                try:
                    raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    return {"error": f"Timeout after {timeout}s"}

                if not raw:
                    log.warning("[pool:%s] stdout EOF (process died)", entry.pool_id)
                    return {"error": "Process terminated unexpectedly"}

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
                    log.debug(
                        "[pool:%s] init: model=%s", entry.pool_id, data.get("model")
                    )

                if msg_type == "assistant":
                    blocks = data.get("message", {}).get("content", [])
                    texts = [b["text"] for b in blocks if b.get("type") == "text"]
                    if texts:
                        result_text = "\n\n".join(texts)

                if msg_type == "result":
                    if not session_id:
                        session_id = data.get("session_id", "")
                    if session_id and entry.session_id != session_id:
                        entry.session_id = session_id

                    result_text = data.get("result") or result_text
                    log.info(
                        "[pool:%s] response: %d chars, %dms",
                        entry.pool_id,
                        len(result_text),
                        data.get("duration_ms", 0),
                    )

                    if data.get("is_error", False):
                        subtype = data.get("subtype", "")
                        if subtype == "error_max_turns" and result_text:
                            return {
                                "result": result_text,
                                "session_id": session_id or "",
                                "warning": "Response truncated (max turns reached)",
                            }
                        return {"error": result_text or "Unknown error from Claude"}

                    return {"result": result_text, "session_id": session_id or ""}

        except Exception as exc:
            log.exception("[pool:%s] read error: %s", entry.pool_id, exc)
            return {"error": f"Read error: {type(exc).__name__}"}

    async def _kill(self, pool_id: str) -> None:
        entry = self._entries.pop(pool_id, None)
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
                to_kill = [
                    pid
                    for pid, e in self._entries.items()
                    if not e.is_alive() or (now - e.last_activity) > self._idle_ttl
                ]
                for pool_id in to_kill:
                    e = self._entries.get(pool_id)
                    reason = "idle" if e and e.is_alive() else "dead"
                    log.info("[pool:%s] reaping %s process", pool_id, reason)
                    await self._kill(pool_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("idle reaper error: %s", exc)
