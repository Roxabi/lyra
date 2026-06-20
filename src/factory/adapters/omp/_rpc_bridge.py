"""RpcBridge — wires omp_rpc RpcClient to NATS publish paths.

Responsibilities:
- Digest gate: verify the omp binary sha256 before RpcClient construction.
- Public lifecycle API: attach, register, run, steer, aclose.
- Callback + steer wiring delegated to _rpc_bridge_callbacks / _rpc_bridge_steer.

ADR: ADR-073 (SanitizedError discipline) — all bus-bound error message fields use
     type(exc).__name__ only; never str(exc), f"{exc}", or repr(exc).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.adapters.omp import _rpc_digest
from factory.adapters.omp._rpc_bridge_callbacks import RpcBridgeCallbacksMixin
from factory.adapters.omp._rpc_bridge_steer import (
    RpcBridgeSteerMixin,
    SteerViolationError,
)
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

_classify_exception = classify_exception
_make_progress = make_progress
_make_result = make_result
_worker_error_from_omp_turn = worker_error_from_omp_turn

__all__ = [
    "DigestMismatchError",
    "RpcBridge",
    "SteerViolationError",
    "_DEFAULT_MODEL",
    "_DEFAULT_REQUEST_TIMEOUT",
    "_ENV_REQUEST_TIMEOUT_KEY",
    "_PINNED_SHA256",
    "_classify_exception",
    "_read_request_timeout",
    "publish_job_error",
]


class RpcBridge(RpcBridgeCallbacksMixin, RpcBridgeSteerMixin):
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
        self._wire_callbacks()
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
        self._wire_callbacks()
        await asyncio.to_thread(self._client.new_session)

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
        self._wire_callbacks()
        nc = self._nc

        steer_sub = None
        if nc is not None:
            steer_sub = await self._subscribe_steer(nc, job_id)
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

    async def aclose(self) -> None:
        """Stop the omp_rpc subprocess. Idempotent; no-op if never started."""
        if not self._started:
            return
        self._started = False
        await asyncio.to_thread(self._client.stop)

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
