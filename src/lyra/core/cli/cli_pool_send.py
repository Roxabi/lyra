"""Send-mixin for CliPool — extracted from cli_pool.py (#1588)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from ..agent.agent_config import ModelConfig
from ..ports.audit_sink import AuditSink
from .cli_pool_types import _CliPoolCore
from .protocol.cli_protocol import (
    CliProtocolOptions,
    CliResult,
    send_and_read,
)

if TYPE_CHECKING:
    from .cli_pool_entry import _ProcessEntry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CliPoolDeps:
    """Dependencies for CliPool constructor."""

    idle_ttl: int = 1200
    default_timeout: int = 1200  # 20 min × 3 retries = 60 min max idle
    on_reap: Callable[[str, str], Coroutine[Any, Any, None]] | None = field(
        default=None
    )
    reaper_interval: int = 60
    kill_timeout: float = 5.0
    read_buffer_bytes: int = 1024 * 1024
    stdin_drain_timeout: float = 10.0
    max_idle_retries: int = 3
    intermediate_timeout: float = 5.0
    audit_sink: AuditSink | None = field(default=None)


class CliPoolSendMixin:
    """Mixin providing send() and drain_audit_tasks() for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _entries: dict[str, _ProcessEntry]
        _default_timeout: int
        _protocol_opts: CliProtocolOptions
        _audit_tasks: set[asyncio.Task[None]]

    async def send(  # noqa: C901,PLR0913 — DEBT:complexity-residual
        self,
        pool_id: str,
        message: str,
        model_config: ModelConfig,
        system_prompt: str = "",
        agent_name: str | None = None,
        agent_email: str | None = None,
        lyra_session_id: str | None = None,
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
                entry = await cast(_CliPoolCore, self)._spawn(
                    pool_id,
                    model_config,
                    system_prompt,
                    agent_name=agent_name,
                    agent_email=agent_email,
                    lyra_session_id=lyra_session_id,
                )
                if entry is None:
                    return CliResult(error="Failed to spawn Claude CLI process")
            elif entry.system_prompt != system_prompt:
                log.info(
                    "[pool:%s] system_prompt changed — respawning process",
                    pool_id,
                )
                await cast(_CliPoolCore, self)._kill(pool_id, preserve_session=False)
                entry = await cast(_CliPoolCore, self)._spawn(
                    pool_id,
                    model_config,
                    system_prompt,
                    agent_name=agent_name,
                    agent_email=agent_email,
                    lyra_session_id=lyra_session_id,
                )
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
                        await cast(_CliPoolCore, self)._kill(pool_id)
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
                        await cast(
                            _CliPoolCore, self
                        )._kill(pool_id, preserve_session=False)
                        continue
                    entry.turn_count += 1
                    entry.last_activity = time.time()
                    return result
                except Exception as exc:
                    log.exception("[pool:%s] send failed: %s", pool_id, exc)
                    await cast(_CliPoolCore, self)._kill(pool_id)
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
