"""omp_rpc callback wiring and JobProgress / JobResult event translation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from factory.adapters.omp._rpc_envelope import make_progress, make_result
from factory.adapters.omp._rpc_turn import TurnPublishContext
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs.subjects import jobs_progress, jobs_result

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient

log = logging.getLogger(__name__)


def _log_publish_result(task: asyncio.Task[Any]) -> None:
    """Done-callback for a NATS publish scheduled from an omp_rpc listener thread.

    asyncio task results are otherwise dropped on the floor — without this a failed
    publish (NATS down, ACL denial) would vanish silently. Logs the exception type
    locally only; no bus exposure, so ADR-073 does not apply here (#1876).
    """
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        log.warning("rpc_bridge: scheduled NATS publish failed", exc_info=exc)


class RpcBridgeCallbacksMixin:
    """Callback registration helpers and omp_rpc → NATS event translation."""

    _client: Any | None
    _nc: NatsClient | None
    _loop: asyncio.AbstractEventLoop | None
    _last_agent_end_event: Any | None
    _current_job_id: str | None

    def _derive_assistant_text(self, turn: Any) -> str:
        """Assistant text for the result: ``turn.assistant_text``, falling back to
        the last message of the stored agent-end event (stdout-thread sentinel)."""
        assistant_text = getattr(turn, "assistant_text", None)
        if assistant_text is None:
            end_event = self._last_agent_end_event
            if end_event is not None:
                messages = getattr(end_event, "messages", None)
                if messages:
                    assistant_text = getattr(messages[-1], "assistant_text", None)
        return assistant_text if assistant_text is not None else ""

    async def _publish_turn_outcome(
        self,
        nc: NatsClient,
        job_id: str,
        ctx: TurnPublishContext,
    ) -> None:
        if ctx.turn_error is not None:
            log.warning(
                "rpc_bridge: job %s OMP turn failed (code=%s)",
                job_id,
                ctx.turn_error.code,
            )
            payload = make_result(job_id, status="error", error=ctx.turn_error)
            await nc.publish(jobs_result(job_id), payload)
            return

        text: str = self._derive_assistant_text(ctx.turn)
        if not text:
            log.warning("rpc_bridge: job %s produced an empty result text", job_id)
            empty_error = WorkerError(
                code="worker.validation",
                message="EmptyResponse",
                retryable=False,
            )
            payload = make_result(job_id, status="error", error=empty_error)
            await nc.publish(jobs_result(job_id), payload)
            return

        data: dict[str, Any] = {"result": text}
        if ctx.model_fallback is not None:
            data["model_fallback"] = ctx.model_fallback
        if ctx.session_file is not None:
            data["session_file"] = ctx.session_file
        payload = make_result(job_id, status="success", data=data)
        await nc.publish(jobs_result(job_id), payload)

    def _schedule_publish(self, subject: str, payload: bytes) -> None:
        """Schedule a NATS publish from an omp_rpc listener thread.

        omp_rpc invokes listeners on its stdout-reader daemon thread, which has no
        running event loop — marshal back onto the captured loop via
        call_soon_threadsafe (#1875).
        """
        nc = self._nc
        loop = self._loop
        if nc is None or loop is None:
            return

        def _spawn() -> None:
            # Runs on the loop thread (via call_soon_threadsafe) — create_task is safe.
            task = asyncio.create_task(nc.publish(subject, payload))
            task.add_done_callback(_log_publish_result)

        loop.call_soon_threadsafe(_spawn)

    def _wire_callbacks(self) -> None:
        """Register omp_rpc listeners on the active client."""
        if self._client is None:
            return
        self._client.on_message_update(self._on_message_update)
        self._client.on_tool_execution_start(self._on_tool_execution_start)
        self._client.on_agent_end(self._on_agent_end)

    def _on_message_update(self, event: Any) -> None:
        """Translate on_message_update → factory.job.<job_id>.progress."""
        job_id = getattr(self, "_current_job_id", None)
        if job_id is None:
            return
        partial_text = getattr(event, "text", None)
        payload = make_progress(
            job_id,
            step="message_update",
            event_type="message_update",
            partial_text=partial_text,
            detail={"partial_text": partial_text},
        )
        self._schedule_publish(jobs_progress(job_id), payload)

    def _on_tool_execution_start(self, event: Any) -> None:
        """Translate on_tool_execution_start → factory.job.<job_id>.progress."""
        job_id = getattr(self, "_current_job_id", None)
        if job_id is None:
            return
        tool_name = getattr(event, "tool_name", None)
        tool_id = getattr(event, "tool_id", None)
        # tool_input NOT published — may contain credentials/file fragments (ADR-073)
        payload = make_progress(
            job_id,
            step="tool_start",
            event_type="tool_start",
            tool_name=tool_name,
            tool_id=tool_id,
            detail={"tool_name": tool_name, "tool_id": tool_id},
        )
        self._schedule_publish(jobs_progress(job_id), payload)

    def _on_agent_end(self, event: Any) -> None:
        """Store the agent-end event for use by run() (store-only, no publish).

        run() is the sole success publisher — it reads the completed turn after
        prompt_and_wait returns (race-free, on the event loop). This callback fires
        on the omp_rpc stdout daemon thread BEFORE prompt_and_wait returns, so
        _last_turn is still None here; publishing from this site always shipped empty
        results. We store the event so run() can fall back to event.messages if needed.
        """
        self._last_agent_end_event = event
