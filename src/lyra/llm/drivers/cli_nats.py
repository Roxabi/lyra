"""CliNatsDriver — hub-side LlmProvider dispatching claude-cli over NATS."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from lyra.core.messaging.events import LlmEvent, ResultLlmEvent, TextLlmEvent
from lyra.llm.base import LlmResult
from roxabi_contracts.cli.models import CliCmdPayload, CliControlCmd
from roxabi_nats.driver_base import NatsDriverBase

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.core.agent.agent_config import ModelConfig

__all__ = ["CliNatsDriver"]
log = logging.getLogger(__name__)


class CliNatsDriver(NatsDriverBase):
    """LlmProvider over NATS — hub sends to CliPoolNatsWorker."""

    SUBJECT_CMD = "lyra.clipool.cmd"
    SUBJECT_CONTROL = "lyra.clipool.control"
    HB_SUBJECT = "lyra.clipool.heartbeat"
    capabilities: dict[str, Any] = {"streaming": True, "auth": "nats"}

    def __init__(self, nc: "NATS", *, timeout: float = 120.0) -> None:
        super().__init__(nc, timeout=timeout)

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
                yield ResultLlmEvent(
                    is_error=bool(chunk.get("is_error", False)),
                    duration_ms=int(chunk.get("duration_ms", 0)),
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
        except Exception as exc:
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
        return LlmResult(
            result=reply.get("text") or reply.get("result", ""),
            session_id=reply.get("session_id", ""),
        )

    # ── CliPool-compatible control methods ────────────────────────────────

    async def reset(self, pool_id: str) -> None:
        """Send reset control command to clipool worker."""
        payload = self._build_control_payload(pool_id, "reset")
        await self._request(self.SUBJECT_CONTROL, payload)

    async def resume_and_reset(self, pool_id: str, session_id: str) -> bool:
        """Ask clipool worker to resume a prior session then reset."""
        payload = self._build_control_payload(
            pool_id, "resume_and_reset", session_id=session_id
        )
        reply = await self._request(self.SUBJECT_CONTROL, payload)
        return bool(reply.get("resumed", False))

    async def switch_cwd(self, pool_id: str, cwd: Path) -> None:
        """Ask clipool worker to switch the working directory."""
        payload = self._build_control_payload(pool_id, "switch_cwd", cwd=str(cwd))
        await self._request(self.SUBJECT_CONTROL, payload)

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """No-op on the NATS side — session linkage is carried in each payload."""
        log.debug(
            "cli_nats: link pool_id=%s → lyra_session=%s", pool_id, lyra_session_id
        )

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
            lyra_session_id=pool_id,
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
