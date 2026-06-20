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
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.adapters.omp import _rpc_digest
from factory.adapters.omp._rpc_digest import read_request_timeout, verify_digest
from factory.adapters.omp._rpc_envelope import (
    classify_exception,
    make_progress,
    make_result,
    publish_job_error,
)
from factory.adapters.omp._rpc_turn import (
    TurnPublishContext,
    worker_error_from_omp_turn,
)
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs.subjects import jobs_progress, jobs_result, jobs_steer

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient

log = logging.getLogger(__name__)

_DEFAULT_PROVIDER = (
    "litellm"  # routes through factory LiteLLM proxy (deploy/omp/models.yml)
)
# Pin a fast non-reasoning default — model=None falls through to models.yml[0]
# (grok-4 full) and risks RpcClient(request_timeout=30s) timeouts (#1910).
# Alias must exist in the LiteLLM xAI pass-through catalogue (#1923).
_DEFAULT_MODEL = "grok-4.20-non-reasoning"
_OMP_BIN = _rpc_digest._OMP_BIN
_PINNED_SHA256 = _rpc_digest._PINNED_SHA256
_DEFAULT_REQUEST_TIMEOUT = _rpc_digest._DEFAULT_REQUEST_TIMEOUT
_ENV_REQUEST_TIMEOUT_KEY = _rpc_digest._ENV_REQUEST_TIMEOUT_KEY
DigestMismatchError = _rpc_digest.DigestMismatchError
_read_request_timeout = read_request_timeout
_verify_digest = verify_digest


class SteerViolationError(Exception):
    """Raised when steer() is attempted while prompt_and_wait is in flight."""


_classify_exception = classify_exception
_make_progress = make_progress
_make_result = make_result
_worker_error_from_omp_turn = worker_error_from_omp_turn


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


class RpcBridge:
    """Bridge between omp_rpc.RpcClient and NATS publish calls.

    Lifecycle (pool path):
      1. Construct with an injected, already-started _client (digest gate skipped).
      2. Call attach(nc) — wires callbacks only, no start/new_session.
      3. Call run(prompt, job_id) per job.
      4. Optionally call steer(job_id, text) between tool calls.

    Lifecycle (standalone/legacy path):
      1. Construct without _client (digest gate runs, new RpcClient constructed).
      2. Call register(nc) — start + wire callbacks + new_session.
      3. Call run(prompt, job_id) per job.
    """

    def __init__(
        self,
        omp_bin: Path = _OMP_BIN,
        *,
        provider: str | None = _DEFAULT_PROVIDER,
        model: str | None = None,
        request_timeout: float | None = None,
        _client: Any | None = None,
    ) -> None:
        # Import deferred: omp_rpc is a container image dep, absent from pyproject.toml.
        import omp_rpc  # type: ignore[import-not-found]

        resolved_provider = provider or _DEFAULT_PROVIDER
        resolved_model = model if model is not None else _DEFAULT_MODEL
        if _client is not None:
            # Pool path: adopt the already-started client; skip the digest gate.
            self._client: omp_rpc.RpcClient = _client
        else:
            # Standalone/tests path: verify digest, then construct client.
            _verify_digest(omp_bin)
            # provider/model are constructor kwargs only — no env axis. The runtime
            # model list comes from deploy/omp/models.yml (via PI_CODING_AGENT_DIR),
            # not from OMP_PROVIDER/OMP_MODEL env vars (#1876).
            resolved_timeout = (
                request_timeout
                if request_timeout is not None
                else _read_request_timeout()
            )
            self._client = omp_rpc.RpcClient(
                executable=str(omp_bin),
                provider=resolved_provider,
                model=resolved_model,
                no_session=False,
                request_timeout=resolved_timeout,
            )
        self._provider = resolved_provider
        self._startup_model = resolved_model
        self._nc: NatsClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started: bool = False
        self._in_prompt_await: bool = False
        self._result_sent: bool = False
        self._last_turn: Any | None = None
        self._last_agent_end_event: Any | None = None

    async def attach(
        self, nc: Any, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """Wire callbacks on an already-started client (pool path).

        Replacement for register() on the pool path. The pool has already called
        client.start() and owns session selection via acquire(). This method:
          - sets self._nc and self._loop
          - wires the three omp_rpc callbacks
          - sets self._started = True
        Does NOT call self._client.start() or new_session().
        """
        self._nc = nc
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._client.on_message_update(self._on_message_update)
        self._client.on_tool_execution_start(self._on_tool_execution_start)
        self._client.on_agent_end(self._on_agent_end)
        self._started = True

    async def register(self, nc: NatsClient) -> None:
        """Wire callbacks and open the omp_rpc session.

        Must be called after NATS connection is established and
        before any run() call. Callback registration order is
        mandated by the omp_rpc API (confirmed in spike #1807):
          start() → on_message_update → on_tool_execution_start → on_agent_end
          → new_session() (MUST be last; requires process running — #1875)
        """
        self._nc = nc
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(
            self._client.start
        )  # spawns omp subprocess + ready handshake — MUST precede new_session (#1875)
        self._started = True
        self._client.on_message_update(self._on_message_update)
        self._client.on_tool_execution_start(self._on_tool_execution_start)
        self._client.on_agent_end(self._on_agent_end)
        await asyncio.to_thread(self._client.new_session)

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

    async def _switch_model(self, model_id: str) -> None:
        await asyncio.to_thread(self._client.set_model, self._provider, model_id)

    async def _execute_prompt(self, prompt: str) -> Any:
        return await asyncio.to_thread(self._client.prompt_and_wait, prompt)

    async def _maybe_retry_with_registry_fallback(
        self,
        prompt: str,
        *,
        requested_model: str | None,
        turn: Any,
    ) -> tuple[Any, WorkerError | None, dict[str, str] | None]:
        """Retry with the first catalogue model when the requested model is invalid."""
        from factory.adapters.omp._model_catalogue import first_registry_model

        turn_error = worker_error_from_omp_turn(turn, self._last_agent_end_event)
        if turn_error is None or turn_error.code != "llm.model_unavailable":
            return turn, turn_error, None

        attempted = requested_model or self._startup_model
        fallback = await asyncio.to_thread(first_registry_model)
        if not fallback or fallback == attempted:
            return turn, turn_error, None

        log.warning(
            "rpc_bridge: model %s unavailable — retrying with registry default %s",
            attempted,
            fallback,
        )
        self._last_agent_end_event = None
        await self._switch_model(fallback)
        retry_turn = await self._execute_prompt(prompt)
        self._last_turn = retry_turn
        retry_error = worker_error_from_omp_turn(retry_turn, self._last_agent_end_event)
        if retry_error is not None:
            return retry_turn, retry_error, None
        return retry_turn, None, {"requested": attempted, "fallback": fallback}

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

    async def run(
        self,
        prompt: str,
        job_id: str,
        *,
        session_file: str | None = None,
        model: str | None = None,
    ) -> None:
        """Run a prompt through omp_rpc; publishes progress and result to NATS.

        ``session_file`` – omp session path to include in the JobResult data dict
                           (backhaul — obtained from OmpPool after acquire).

        Stores job_id on self for callback access during prompt_and_wait.
        Subscribes factory.job.<job_id>.steer while prompt_and_wait is in flight;
        unsubscribes unconditionally in the finally block.
        No bare except — exceptions propagate to the caller (OmpWorker.handle).
        """
        self._current_job_id = job_id
        self._in_prompt_await = True
        # run() is the sole success publisher — it publishes after prompt_and_wait
        # returns (race-free, on the event loop). _on_agent_end only stores the event.
        self._result_sent = False
        self._last_turn = None
        self._last_agent_end_event = None  # reset: avoid stale prior-job leak
        # Ensure callbacks are wired on the (possibly injected/pool) client before use.
        # Safe to re-assign; covers both register() (legacy) and attach() (pool) paths.
        if self._client is not None:
            self._client.on_message_update(self._on_message_update)
            self._client.on_tool_execution_start(self._on_tool_execution_start)
            self._client.on_agent_end(self._on_agent_end)
        nc = self._nc

        async def _handle_steer_msg(msg: Any) -> None:
            if not self._in_prompt_await:
                log.debug("rpc_bridge: steer message dropped — no active job")
                return
            text = msg.data.decode("utf-8", errors="replace")
            await asyncio.to_thread(self._client.steer, text)

        steer_sub = None
        if nc is not None:
            steer_sub = await nc.subscribe(jobs_steer(job_id), cb=_handle_steer_msg)
        assert self._client is not None, "no client (register/attach missing)"
        try:
            requested_model = (model or "").strip() or None
            if requested_model:
                await self._switch_model(requested_model)

            turn = await self._execute_prompt(prompt)
            self._last_turn = turn
            (
                turn,
                turn_error,
                model_fallback,
            ) = await self._maybe_retry_with_registry_fallback(
                prompt,
                requested_model=requested_model,
                turn=turn,
            )
            # Publish here — guaranteed to see the completed turn value.
            # _on_agent_end fires on the stdout thread BEFORE prompt_and_wait returns
            # so it cannot safely read _last_turn; this is the only safe publish site.
            if nc is not None and not self._result_sent:
                self._result_sent = True
                await self._publish_turn_outcome(
                    nc,
                    job_id,
                    TurnPublishContext(
                        turn=turn,
                        turn_error=turn_error,
                        model_fallback=model_fallback,
                        session_file=session_file,
                    ),
                )
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
        await asyncio.to_thread(self._client.steer, text)

    async def aclose(self) -> None:
        """Stop the omp_rpc subprocess. Idempotent; no-op if never started."""
        if not self._started:
            return
        self._started = False
        await asyncio.to_thread(self._client.stop)

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

    # ------------------------------------------------------------------
    # omp_rpc callbacks
    # ------------------------------------------------------------------

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

    async def publish_error(self, job_id: str, exc: BaseException) -> None:
        """Publish a JobResult(status=error) for a failed job.

        No-op if _result_sent is True — guards against double-publish when
        _on_agent_end fires and prompt_and_wait also raises.
        Thin wrapper around the module-level publish_job_error.
        """
        if self._result_sent:
            log.debug(
                "rpc_bridge: publish_error skipped — result already sent for %s", job_id
            )
            return
        self._result_sent = True
        nc = self._nc
        if nc is None:
            log.warning("rpc_bridge: cannot publish error — nc not set")
            return
        await publish_job_error(nc, job_id, exc)
