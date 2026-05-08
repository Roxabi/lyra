"""CliNatsDriver — hub-side LlmProvider dispatching claude-cli over NATS."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import uuid4

import nats.errors

from lyra.core.messaging.events import LlmEvent, ResultLlmEvent, TextLlmEvent
from lyra.llm.base import LlmResult
from roxabi_contracts.cli.models import CliCmdPayload, CliControlCmd
from roxabi_nats.driver_base import NatsDriverBase

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.core.agent.agent_config import ModelConfig


class _CliSessionStore(Protocol):
    """Minimal protocol for TurnStore operations needed by CliNatsDriver."""

    async def set_cli_session(self, session_id: str, cli_session_id: str) -> None: ...

    async def get_cli_session(self, session_id: str) -> str | None: ...


__all__ = ["CliNatsDriver"]
log = logging.getLogger(__name__)


def _log_task_exc(task: asyncio.Task) -> None:
    """Done-callback: log any exception from a fire-and-forget task."""
    if not task.cancelled() and (exc := task.exception()):
        log.error(
            "cli_nats: fire-and-forget task %r failed",
            task.get_name(),
            exc_info=exc,
        )


class CliNatsDriver(NatsDriverBase):
    """LlmProvider over NATS — hub sends to CliPoolNatsWorker."""

    SUBJECT_CMD = "lyra.clipool.cmd"
    SUBJECT_CONTROL = "lyra.clipool.control"
    HB_SUBJECT = "lyra.clipool.heartbeat"
    capabilities: dict[str, Any] = {"streaming": True, "auth": "nats"}

    def __init__(
        self,
        nc: "NATS",
        *,
        timeout: float = 120.0,
        max_total_duration: float | None = None,
    ) -> None:
        super().__init__(nc, timeout=timeout, max_total_duration=max_total_duration)
        self._lyra_sessions: dict[str, str] = {}
        self._turn_store: _CliSessionStore | None = None

    def set_turn_store(self, store: _CliSessionStore) -> None:
        """Wire the hub TurnStore so the driver can persist cli_session_id mappings."""
        self._turn_store = store

    # ── LlmProvider protocol ──────────────────────────────────────────────

    async def stream(
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Return an async generator of LlmEvents for a streaming clipool request."""
        return self._stream_gen_llm(pool_id, text, model_cfg, system_prompt)

    async def _stream_gen_llm(
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
    ) -> AsyncIterator[LlmEvent]:
        """Async generator: yield LlmEvents from the clipool worker via NATS inbox."""
        payload = self._build_cmd_payload(
            pool_id, text, model_cfg, system_prompt, stream=True
        )
        async for chunk in self._stream_gen(self.SUBJECT_CMD, payload):
            event_type = chunk.get("event_type", "text")
            if event_type == "text":
                t = chunk.get("text") or ""
                if t:
                    yield TextLlmEvent(text=t)
            elif event_type == "result":
                _cli_sid = chunk.get("session_id")
                if _cli_sid and self._turn_store and pool_id in self._lyra_sessions:
                    self._fire_set_cli_session(self._lyra_sessions[pool_id], _cli_sid)
                _is_error = bool(chunk.get("is_error", False))
                yield ResultLlmEvent(
                    is_error=_is_error,
                    duration_ms=int(chunk.get("duration_ms", 0)),
                    session_id=_cli_sid or None,
                    error_text=chunk.get("error_text") or None if _is_error else None,
                )
                return
            if chunk.get("done", False):
                # Defensive: worker set done=True on a non-result chunk.
                log.warning(
                    "cli_nats: worker sent done=True on event_type=%r [pool:%s]",
                    event_type,
                    pool_id,
                )
                yield ResultLlmEvent(is_error=False, duration_ms=0)
                return

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        """Dispatch a single-turn completion to clipool over NATS request-reply."""
        payload = self._build_cmd_payload(
            pool_id, text, model_cfg, system_prompt, stream=False
        )
        try:
            reply = await self._request(self.SUBJECT_CMD, payload)
        except nats.errors.Error as exc:
            log.warning(
                "cli_nats: complete() transport error [pool:%s]: %s: %s",
                pool_id,
                type(exc).__name__,
                exc,
            )
            return LlmResult(error=f"NATS transport error: {exc}", retryable=True)

        error = reply.get("error", "")
        if error:
            return LlmResult(
                error=error,
                retryable=bool(reply.get("retryable", True)),
            )
        _cli_sid = reply.get("session_id", "")
        if _cli_sid and self._turn_store and pool_id in self._lyra_sessions:
            self._fire_set_cli_session(self._lyra_sessions[pool_id], _cli_sid)
        return LlmResult(
            result=reply.get("text") or reply.get("result", ""),
            session_id=_cli_sid or "",
        )

    # ── CliPool-compatible control methods ────────────────────────────────

    async def reset(self, pool_id: str) -> None:
        """Send reset control command to clipool worker."""
        payload = self._build_control_payload(pool_id, "reset")
        await self._request(self.SUBJECT_CONTROL, payload)

    async def resume_and_reset(self, pool_id: str, session_id: str) -> bool:
        """Ask clipool worker to resume a prior session then reset.

        Looks up the cli_session_id from hub TurnStore and sends it to the worker
        so the worker can pass it directly to CliPool.resume_direct (no TurnStore
        lookup on the worker side).
        """
        cli_sid: str | None = None
        if self._turn_store is not None:
            cli_sid = await self._turn_store.get_cli_session(session_id)
        if cli_sid is None:
            log.debug(
                "cli_nats: no cli_session_id for lyra_session=%s, "
                "skipping resume [pool:%s]",
                session_id,
                pool_id,
            )
            return False
        payload = self._build_control_payload(
            pool_id, "resume_and_reset", session_id=cli_sid
        )
        reply = await self._request(self.SUBJECT_CONTROL, payload)
        return bool(reply.get("resumed", False))

    async def switch_cwd(self, pool_id: str, cwd: Path) -> None:
        """Ask clipool worker to switch the working directory."""
        payload = self._build_control_payload(pool_id, "switch_cwd", cwd=str(cwd))
        await self._request(self.SUBJECT_CONTROL, payload)

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """Store lyra_session_id for pool_id; _build_cmd_payload uses it."""
        self._lyra_sessions[pool_id] = lyra_session_id
        log.debug(
            "cli_nats: link pool_id=%s → lyra_session=%s", pool_id, lyra_session_id
        )

    def unlink_lyra_session(self, pool_id: str) -> None:
        """Remove the pool_id → lyra_session_id mapping (call on pool eviction)."""
        self._lyra_sessions.pop(pool_id, None)

    def _fire_set_cli_session(self, lyra_sid: str, cli_sid: str) -> None:
        """Schedule set_cli_session without blocking; log any failure."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.error(
                "cli_nats: _fire_set_cli_session called outside event loop [%s]",
                lyra_sid[:8],
            )
            return
        task = loop.create_task(
            self._turn_store.set_cli_session(lyra_sid, cli_sid),  # type: ignore[union-attr]
            name=f"set_cli_session:{lyra_sid[:8]}",
        )
        task.add_done_callback(_log_task_exc)

    # ── Payload builders ──────────────────────────────────────────────────

    def _build_cmd_payload(
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        stream: bool,
    ) -> dict:
        return CliCmdPayload(
            contract_version="1",
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            lyra_session_id=self._lyra_sessions.get(pool_id, pool_id),
            text=text,
            model_cfg=model_cfg.model_dump(exclude={"api_key"}),
            system_prompt=system_prompt,
            stream=stream,
        ).model_dump(mode="json")

    def _build_control_payload(
        self,
        pool_id: str,
        op: Literal["reset", "resume_and_reset", "switch_cwd"],
        *,
        session_id: str | None = None,
        cwd: str | None = None,
    ) -> dict:
        return CliControlCmd(
            contract_version="1",
            trace_id=str(uuid4()),
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            op=op,
            session_id=session_id,
            cwd=cwd,
        ).model_dump(mode="json")
