"""RpcBridge — wires omp_rpc RpcClient to NATS publish paths.

Responsibilities:
- Digest gate: verify the omp binary sha256 before RpcClient construction.
- Callback registration: on_message_update, on_tool_execution_start, on_agent_end.
- Event translation: omp_rpc callbacks → JobProgress / JobResult publishes.
- Steer bridge: subscribe factory.job.<job_id>.steer; guard against steer-inside-await.

ADR: ADR-073 (SanitizedError discipline) — all bus-bound error message fields use
     type(exc).__name__ only; never str(exc), f"{exc}", or repr(exc).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs.models import JobProgress, JobResult
from roxabi_contracts.jobs.subjects import jobs_progress, jobs_result, jobs_steer

if TYPE_CHECKING:
    import nats

log = logging.getLogger(__name__)

_PINNED_SHA256 = "b877091c91ebdc8c8d907c4b62681895cd3ae049815858aea7b69ac1d53b7c7b"
_OMP_BIN = Path("/opt/omp/omp")  # image-build constant — NEVER from env


class DigestMismatchError(Exception):
    """Raised when the omp binary sha256 does not match the pinned value."""

    def __init__(self, actual: str, expected: str) -> None:
        super().__init__(f"digest mismatch: actual={actual} expected={expected}")
        self.actual = actual
        self.expected = expected


class SteerViolationError(Exception):
    """Raised when steer() is attempted while prompt_and_wait is in flight."""


def _classify_exception(exc: BaseException) -> WorkerError:
    """Map an exception to a WorkerError.

    SanitizedError discipline (ADR-073): message field = type(exc).__name__ only.
    """
    name = type(exc).__name__
    if name == "DigestMismatchError":
        return WorkerError(code="digest_mismatch", message=name, retryable=False)
    if isinstance(exc, asyncio.TimeoutError):
        return WorkerError(code="timeout", message=name, retryable=True)
    if name in ("ConnectionRefusedError", "BrokenPipeError"):
        return WorkerError(code="connection_error", message=name, retryable=True)
    return WorkerError(code="internal_error", message=name, retryable=False)


def _make_progress(job_id: str, **kwargs: Any) -> bytes:
    """Serialise a JobProgress to JSON bytes."""
    event = JobProgress(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        job_id=job_id,
        **kwargs,
    )
    return event.model_dump_json().encode()


def _make_result(job_id: str, **kwargs: Any) -> bytes:
    """Serialise a JobResult to JSON bytes."""
    event = JobResult(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        job_id=job_id,
        **kwargs,
    )
    return event.model_dump_json().encode()


def _verify_digest(omp_bin: Path) -> None:
    """Hash the omp binary and raise DigestMismatchError on mismatch."""
    hasher = hashlib.sha256()
    with omp_bin.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != _PINNED_SHA256:
        raise DigestMismatchError(actual=actual, expected=_PINNED_SHA256)


class RpcBridge:
    """Bridge between omp_rpc.RpcClient and NATS publish calls.

    Lifecycle:
      1. Construct (runs digest gate — CPU-blocking hash read).
      2. Call register(nc) once connected — wires callbacks + opens session.
      3. Call run(prompt, job_id) per job.
      4. Optionally call steer(job_id, text) between tool calls.
    """

    def __init__(
        self,
        omp_bin: Path = _OMP_BIN,
    ) -> None:
        _verify_digest(omp_bin)

        # Import deferred: omp_rpc is a container image dep, absent from pyproject.toml.
        import omp_rpc  # type: ignore[import-not-found]

        self._client: omp_rpc.RpcClient = omp_rpc.RpcClient()
        self._nc: nats.aio.client.Client | None = None
        self._in_prompt_await: bool = False
        self._result_sent: bool = False

    async def register(self, nc: nats.aio.client.Client) -> None:
        """Wire callbacks and open the omp_rpc session.

        Must be called after NATS connection is established and
        before any run() call. Callback registration order is
        mandated by the omp_rpc API (confirmed in spike #1807):
          on_message_update → on_tool_execution_start → on_agent_end
          → new_session() (MUST be last)
        """
        self._nc = nc
        self._client.on_message_update(self._on_message_update)
        self._client.on_tool_execution_start(self._on_tool_execution_start)
        self._client.on_agent_end(self._on_agent_end)
        await self._client.new_session()

    async def run(self, prompt: str, job_id: str) -> None:
        """Run a prompt through omp_rpc; publishes progress and result to NATS.

        Stores job_id on self for callback access during prompt_and_wait.
        Subscribes factory.job.<job_id>.steer while prompt_and_wait is in flight;
        unsubscribes unconditionally in the finally block.
        No bare except — exceptions propagate to the caller (OmpWorker.handle).
        """
        self._current_job_id = job_id
        self._in_prompt_await = True
        self._result_sent = False
        nc = self._nc

        async def _handle_steer_msg(msg: Any) -> None:
            if not self._in_prompt_await:
                log.debug("rpc_bridge: steer message dropped — no active job")
                return
            text = msg.data.decode("utf-8", errors="replace")
            await self._client.steer(text)

        steer_sub = None
        if nc is not None:
            steer_sub = await nc.subscribe(jobs_steer(job_id), cb=_handle_steer_msg)
        try:
            await self._client.prompt_and_wait(prompt)
        finally:
            self._in_prompt_await = False
            if steer_sub is not None:
                await steer_sub.unsubscribe()

    async def steer(self, job_id: str, text: str) -> None:
        """Fire-and-forget steer; must NOT be called inside prompt_and_wait."""
        if self._in_prompt_await:
            raise SteerViolationError(
                "steer() called while prompt_and_wait is in flight"
            )
        await self._client.steer(text)

    # ------------------------------------------------------------------
    # omp_rpc callbacks
    # ------------------------------------------------------------------

    def _on_message_update(self, event: Any) -> None:
        """Translate on_message_update → factory.job.<job_id>.progress."""
        nc = self._nc
        job_id = getattr(self, "_current_job_id", None)
        if nc is None or job_id is None:
            return
        partial_text = getattr(event, "text", None)
        payload = _make_progress(
            job_id,
            step="message_update",
            event_type="message_update",
            partial_text=partial_text,
            detail={"partial_text": partial_text},
        )
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(nc.publish(jobs_progress(job_id), payload))
        )

    def _on_tool_execution_start(self, event: Any) -> None:
        """Translate on_tool_execution_start → factory.job.<job_id>.progress."""
        nc = self._nc
        job_id = getattr(self, "_current_job_id", None)
        if nc is None or job_id is None:
            return
        tool_name = getattr(event, "tool_name", None)
        tool_id = getattr(event, "tool_id", None)
        # tool_input intentionally NOT published — may contain credentials/file fragments
        payload = _make_progress(
            job_id,
            step="tool_start",
            event_type="tool_start",
            tool_name=tool_name,
            tool_id=tool_id,
            detail={"tool_name": tool_name, "tool_id": tool_id},
        )
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(nc.publish(jobs_progress(job_id), payload))
        )

    def _on_agent_end(self, event: Any) -> None:
        """Translate on_agent_end → factory.job.<job_id>.result (success)."""
        nc = self._nc
        job_id = getattr(self, "_current_job_id", None)
        if nc is None or job_id is None:
            return
        if self._result_sent:
            log.debug("rpc_bridge: _on_agent_end skipped — result already sent for %s", job_id)
            return
        self._result_sent = True
        result_data = getattr(event, "result", None)
        data: dict[str, Any] = {"result": result_data} if result_data is not None else {}
        payload = _make_result(job_id, status="success", data=data)
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(nc.publish(jobs_result(job_id), payload))
        )

    async def publish_error(self, job_id: str, exc: BaseException) -> None:
        """Publish a JobResult(status=error) for a failed job.

        No-op if _result_sent is True — guards against double-publish when
        _on_agent_end fires and prompt_and_wait also raises.
        """
        if self._result_sent:
            log.debug("rpc_bridge: publish_error skipped — result already sent for %s", job_id)
            return
        self._result_sent = True
        nc = self._nc
        if nc is None:
            log.warning("rpc_bridge: cannot publish error — nc not set")
            return
        worker_error = _classify_exception(exc)
        payload = _make_result(job_id, status="error", error=worker_error)
        await nc.publish(jobs_result(job_id), payload)
