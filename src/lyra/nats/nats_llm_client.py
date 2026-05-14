"""NatsLlmClient — hub-side NATS client for LLM generation.

Maintains a ``WorkerRegistry`` populated from heartbeats. Publishes each
generation request to the canonical literal subject ``lyra.llm.generate.request``
— the NATS broker dispatches to a worker via the ``llm-workers`` queue group.

Per-worker score-routed subjects (``lyra.llm.generate.request.{worker_id}``)
were dropped in lyra#1104 to match the canonical ACL allow list. Score-routing
will return when finishing the LlmProvider conformance + bootstrap migration
tracked in lyra#1119.

Implements the ``LlmProvider`` protocol — future replacement candidate for
``NatsLlmDriver`` using ADR-049 Pydantic contracts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import nats.errors
from nats.aio.client import Client as NATS
from nats.errors import NoRespondersError
from pydantic import ValidationError

from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
)
from lyra.core.messaging.metrics import emit_populated_total
from lyra.core.ports.llm import LlmResult
from lyra.nats.worker_registry import WorkerRegistry
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError
from roxabi_contracts.llm import (
    SUBJECTS,
    LlmChunkEvent,
    LlmRequest,
    LlmResponse,
    validate_worker_id,
)
from roxabi_nats.circuit_breaker import NatsCircuitBreaker

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import ModelConfig

log = logging.getLogger(__name__)

_TIMEOUT_DEFAULT = 120.0
_TIMEOUT_MIN = 5.0
_TIMEOUT_MAX = 600.0


def _parse_llm_timeout(timeout: float | None) -> float:
    if timeout is not None:
        value = timeout
    else:
        raw = os.environ.get("LYRA_LLM_TIMEOUT", str(_TIMEOUT_DEFAULT))
        try:
            value = float(raw)
        except ValueError:
            log.warning(
                "LYRA_LLM_TIMEOUT=%r is not a valid float; using %.0fs",
                raw,
                _TIMEOUT_DEFAULT,
            )
            return _TIMEOUT_DEFAULT
    if not (_TIMEOUT_MIN <= value <= _TIMEOUT_MAX):
        log.warning(
            "LLM timeout %.1fs out of range [%.0f, %.0f]; using %.0fs",
            value,
            _TIMEOUT_MIN,
            _TIMEOUT_MAX,
            _TIMEOUT_DEFAULT,
        )
        return _TIMEOUT_DEFAULT
    return value


class NatsLlmClient:
    """Hub-side NATS client for LLM generation.

    Usage::

        client = NatsLlmClient(nc)
        await client.start()

        # streaming — LlmProvider protocol
        async for event in client.stream("pool-1", "hello", mc, "sys"):
            ...

        # non-streaming — LlmProvider protocol
        result = await client.complete("pool-1", "hello", mc, "sys")

        await client.stop()
    """

    capabilities: dict[str, Any] = {"streaming": True, "auth": "nats"}

    def __init__(self, nc: NATS, *, timeout: float | None = None) -> None:
        self._nc = nc
        self._timeout = _parse_llm_timeout(timeout)
        self._cb = NatsCircuitBreaker()
        self._registry = WorkerRegistry()
        self._hb_sub = None

    async def start(self) -> None:
        if self._hb_sub is None:
            self._hb_sub = await self._nc.subscribe(
                SUBJECTS.heartbeat, cb=self._on_heartbeat
            )

    async def stop(self) -> None:
        if self._hb_sub is not None:
            await self._hb_sub.unsubscribe()
            self._hb_sub = None

    def is_alive(self, pool_id: str) -> bool:
        """Return True when NATS is connected and at least one worker is fresh."""
        del pool_id  # LlmProvider protocol slot; client is stateless per-pool
        return self._nc.is_connected and self._registry.any_alive()

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _on_heartbeat(self, msg: Any) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            log.debug("llm_client: heartbeat parse error", exc_info=True)
            return
        worker_id = data.get("worker_id")
        if not worker_id or not isinstance(worker_id, str):
            log.warning("llm_client: heartbeat missing/invalid worker_id, ignoring")
            return
        try:
            validate_worker_id(worker_id)
        except ValueError:
            log.warning(
                "llm_client: heartbeat unsafe worker_id=%r, ignoring", worker_id
            )
            return
        self._registry.record_heartbeat(data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_request(
        self,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        messages: list[dict] | None,
        *,
        stream: bool,
    ) -> tuple[bytes, str]:
        """Build a canonical LlmRequest payload and return (bytes, trace_id).

        Text-folding rule (spec §"Expected Behavior" §3):
          - messages None or empty → publish [{"role": "user", "content": text}]
          - last message content == text → pass messages through unchanged
          - else → append {"role": "user", "content": text} to a copy of messages
        """
        if not messages:
            wire_messages: list[dict] = [{"role": "user", "content": text}]
        elif messages[-1].get("content") == text:
            wire_messages = list(messages)
        else:
            wire_messages = list(messages) + [{"role": "user", "content": text}]

        trace_id = str(uuid4())
        request = LlmRequest(
            contract_version=CONTRACT_VERSION,
            trace_id=trace_id,
            issued_at=datetime.now(timezone.utc),
            request_id=str(uuid4()).replace("-", "")[:32],
            messages=wire_messages,
            model=model_cfg.model,
            system_prompt=system_prompt,
            stream=stream,
            max_tokens=getattr(model_cfg, "max_tokens", None),
            temperature=getattr(model_cfg, "temperature", None),
        )
        payload = request.model_dump_json(exclude_none=True).encode()
        return payload, trace_id

    # ------------------------------------------------------------------
    # Public API — LlmProvider protocol
    # ------------------------------------------------------------------

    async def complete(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> LlmResult:
        """Non-streaming completion. Returns LlmResult with canonical WorkerError."""
        del pool_id  # canonical wire is queue-group dispatched, no per-worker routing

        if self._cb.is_open():
            emit_populated_total(domain="llm")
            return LlmResult(
                error="LLM circuit open — adapter temporarily unavailable",
                retryable=True,
                worker_error=WorkerError(
                    code="worker.capacity",
                    message="LLM circuit open — adapter temporarily unavailable",
                    retryable=True,
                ),
            )

        payload, trace_id = self._build_request(
            text, model_cfg, system_prompt, messages, stream=False
        )
        return await self._complete_request(payload, trace_id)

    async def stream(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        pool_id: str,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Return an async generator of LlmEvents for a streaming request."""
        del pool_id  # canonical wire is queue-group dispatched, no per-worker routing
        return self._stream_gen(text, model_cfg, system_prompt, messages=messages)

    # ------------------------------------------------------------------
    # Internal — complete() transport
    # ------------------------------------------------------------------

    async def _complete_request(  # noqa: C901 — DEBT:complexity-residual
        self, payload: bytes, trace_id: str
    ) -> LlmResult:
        """Execute a single non-streaming NATS request-reply, returning LlmResult.

        All error paths return a populated LlmResult(worker_error=...) — never raise.
        contract_mismatch (CONTRACT_VERSION check) is intentionally deferred:
        the current LlmResponse schema accepts any contract_version at the Pydantic
        layer; a strict version check requires an API version negotiation protocol
        that is out of scope for #1119 Wave 3. Tests covering this case are xfail.
        """
        candidates = self._registry.ordered_by_score()
        if not candidates:
            # No live workers — synthesise a transport.no_responders result.
            # This mirrors the nats.errors.NoRespondersError path: the broker
            # would return no-responders if we published, so we short-circuit.
            error_msg = "LLM: no live worker (heartbeat stale >15s)"
            emit_populated_total(domain="llm")
            return LlmResult(
                error=error_msg,
                retryable=True,
                worker_error=WorkerError(
                    code="transport.no_responders",
                    message=error_msg,
                    retryable=True,
                ),
            )

        last_result: LlmResult | None = None
        for worker in candidates:
            target = SUBJECTS.generate_request
            try:
                reply = await self._nc.request(
                    target, payload, timeout=self._timeout
                )
            except TimeoutError as exc:
                self._registry.mark_stale(worker.worker_id)
                error_msg = f"LLM worker timeout after {self._timeout:.0f}s"
                emit_populated_total(domain="llm")
                last_result = LlmResult(
                    error=error_msg,
                    retryable=True,
                    worker_error=WorkerError(
                        code="transport.timeout",
                        message=str(exc) or error_msg,
                        retryable=True,
                    ),
                )
                continue
            except NoRespondersError as exc:
                self._registry.mark_stale(worker.worker_id)
                error_msg = f"NATS no responders: {exc}"
                emit_populated_total(domain="llm")
                last_result = LlmResult(
                    error=error_msg,
                    retryable=True,
                    worker_error=WorkerError(
                        code="transport.no_responders",
                        message=str(exc) or error_msg,
                        retryable=True,
                    ),
                )
                continue
            except nats.errors.Error as exc:
                # max_payload is a non-retryable hard limit; everything else retries.
                if "max_payload" in str(exc).lower():
                    log.error(
                        "LLM payload too large (%.0f KB)", len(payload) / 1024
                    )
                    self._cb.record_failure()
                    error_msg = f"LLM request payload too large: {exc}"
                    emit_populated_total(domain="llm")
                    return LlmResult(
                        error=error_msg,
                        retryable=False,
                        worker_error=WorkerError(
                            code="transport.error",
                            message=str(exc) or error_msg,
                            retryable=False,
                        ),
                    )
                log.warning(
                    "LLM adapter unreachable: %s: %s", type(exc).__name__, exc
                )
                self._cb.record_failure()
                error_msg = f"NATS transport error: {exc}"
                emit_populated_total(domain="llm")
                return LlmResult(
                    error=error_msg,
                    retryable=True,
                    worker_error=WorkerError(
                        code="transport.error",
                        message=str(exc) or error_msg,
                        retryable=True,
                    ),
                )

            # Parse reply
            try:
                resp = LlmResponse.model_validate_json(reply.data)
            except (ValidationError, ValueError) as exc:
                self._cb.record_failure()
                error_msg = f"Invalid response from worker: {exc}"
                emit_populated_total(domain="llm")
                return LlmResult(
                    error=error_msg,
                    retryable=False,
                    worker_error=WorkerError(
                        code="transport.parse",
                        message=str(exc) or error_msg,
                        retryable=False,
                    ),
                )

            # Worker-reported errors
            if not resp.ok:
                self._cb.record_failure()
                error_msg = resp.error or "LLM generation failed"
                if resp.worker_error is not None:
                    # Propagate structured envelope verbatim (trust the worker).
                    return LlmResult(
                        error=error_msg,
                        retryable=resp.worker_error.retryable,
                        worker_error=resp.worker_error,
                    )
                # Legacy worker: synthesise worker.internal fallback.
                emit_populated_total(domain="llm")
                return LlmResult(
                    error=error_msg,
                    retryable=True,
                    worker_error=WorkerError(
                        code="worker.internal",
                        message=error_msg,
                        retryable=True,
                    ),
                )

            # Success
            self._cb.record_success()
            return LlmResult(
                result=resp.text or "",
                session_id=trace_id,
                error="",
                retryable=True,
            )

        # All candidates exhausted — return the last transport error collected.
        self._cb.record_failure()
        if last_result is not None:
            return last_result
        error_msg = "LLM: all workers unresponsive"
        emit_populated_total(domain="llm")
        return LlmResult(
            error=error_msg,
            retryable=True,
            worker_error=WorkerError(
                code="transport.no_responders",
                message=error_msg,
                retryable=True,
            ),
        )

    # ------------------------------------------------------------------
    # Internal — stream() transport
    # ------------------------------------------------------------------

    async def _stream_gen(  # noqa: C901, PLR0915 — DEBT:complexity-residual
        self,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Async generator: yield LlmEvents from an ephemeral inbox subscription.

        All error paths yield a terminal ResultLlmEvent(is_error=True, worker_error=...)
        then return — never raise to the caller.
        """
        if self._cb.is_open():
            emit_populated_total(domain="llm")
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text="LLM circuit open — adapter temporarily unavailable",
                worker_error=WorkerError(
                    code="worker.capacity",
                    message="LLM circuit open — adapter temporarily unavailable",
                    retryable=True,
                ),
            )
            return

        candidates = self._registry.ordered_by_score()
        if not candidates:
            error_msg = "LLM: no live worker (heartbeat stale >15s)"
            emit_populated_total(domain="llm")
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                cost_usd=None,
                error_text=error_msg,
                worker_error=WorkerError(
                    code="transport.no_responders",
                    message=error_msg,
                    retryable=True,
                ),
            )
            return

        payload, _trace_id = self._build_request(
            text, model_cfg, system_prompt, messages, stream=True
        )

        inbox = self._nc.new_inbox()
        sub = await self._nc.subscribe(inbox)
        try:
            # Publish — transport errors here terminate the stream immediately.
            try:
                await self._nc.publish(
                    SUBJECTS.generate_request, payload, reply=inbox
                )
            except NoRespondersError as exc:
                self._cb.record_failure()
                error_msg = f"NATS no responders: {exc}"
                emit_populated_total(domain="llm")
                yield ResultLlmEvent(
                    is_error=True,
                    duration_ms=0,
                    cost_usd=None,
                    error_text=error_msg,
                    worker_error=WorkerError(
                        code="transport.no_responders",
                        message=str(exc) or error_msg,
                        retryable=True,
                    ),
                )
                return
            except nats.errors.Error as exc:
                self._cb.record_failure()
                if "max_payload" in str(exc).lower():
                    log.error(
                        "LLM stream payload too large (%.0f KB)",
                        len(payload) / 1024,
                    )
                    error_msg = f"LLM request payload too large: {exc}"
                    emit_populated_total(domain="llm")
                    yield ResultLlmEvent(
                        is_error=True,
                        duration_ms=0,
                        cost_usd=None,
                        error_text=error_msg,
                        worker_error=WorkerError(
                            code="transport.error",
                            message=str(exc) or error_msg,
                            retryable=False,
                        ),
                    )
                    return
                error_msg = f"NATS transport error: {exc}"
                emit_populated_total(domain="llm")
                yield ResultLlmEvent(
                    is_error=True,
                    duration_ms=0,
                    cost_usd=None,
                    error_text=error_msg,
                    worker_error=WorkerError(
                        code="transport.error",
                        message=str(exc) or error_msg,
                        retryable=True,
                    ),
                )
                return

            # Consume chunks from the inbox.
            while True:
                try:
                    msg = await sub.next_msg(timeout=self._timeout)
                except (TimeoutError, asyncio.TimeoutError) as exc:
                    if candidates:
                        self._registry.mark_stale(candidates[0].worker_id)
                    self._cb.record_failure()
                    error_msg = f"LLM stream timed out: {exc}"
                    emit_populated_total(domain="llm")
                    yield ResultLlmEvent(
                        is_error=True,
                        duration_ms=0,
                        cost_usd=None,
                        error_text=error_msg,
                        worker_error=WorkerError(
                            code="transport.timeout",
                            message=str(exc) or error_msg,
                            retryable=True,
                        ),
                    )
                    return

                try:
                    chunk = LlmChunkEvent.model_validate_json(msg.data)
                except (ValidationError, ValueError) as exc:
                    self._cb.record_failure()
                    error_msg = f"LLM stream: malformed chunk: {exc}"
                    emit_populated_total(domain="llm")
                    yield ResultLlmEvent(
                        is_error=True,
                        duration_ms=0,
                        cost_usd=None,
                        error_text=error_msg,
                        worker_error=WorkerError(
                            code="transport.parse",
                            message=str(exc) or error_msg,
                            retryable=False,
                        ),
                    )
                    return

                if chunk.is_error:
                    self._cb.record_failure()
                    if chunk.worker_error is not None:
                        # Propagate structured envelope verbatim.
                        yield ResultLlmEvent(
                            is_error=True,
                            duration_ms=chunk.duration_ms or 0,
                            cost_usd=None,
                            error_text=chunk.error or "LLM stream error",
                            worker_error=chunk.worker_error,
                        )
                    else:
                        # Legacy worker: synthesise worker.internal fallback.
                        error_msg = chunk.error or "LLM stream error"
                        emit_populated_total(domain="llm")
                        yield ResultLlmEvent(
                            is_error=True,
                            duration_ms=chunk.duration_ms or 0,
                            cost_usd=None,
                            error_text=error_msg,
                            worker_error=WorkerError(
                                code="worker.internal",
                                message=error_msg,
                                retryable=True,
                            ),
                        )
                    return

                if chunk.delta:
                    yield TextLlmEvent(text=chunk.delta)

                if chunk.done:
                    self._cb.record_success()
                    yield ResultLlmEvent(
                        is_error=False,
                        duration_ms=chunk.duration_ms or 0,
                        cost_usd=None,
                    )
                    return

        finally:
            await sub.unsubscribe()
