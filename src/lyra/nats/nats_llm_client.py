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

import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, NoReturn
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
from lyra.core.ports.llm import LlmResult, LlmUnavailableError
from lyra.nats.worker_registry import WorkerRegistry
from roxabi_contracts.envelope import CONTRACT_VERSION
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
        async for event in await client.stream("pool-1", "hello", mc, "sys"):
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
            system_prompt=system_prompt or None,
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
        """Non-streaming completion. Returns LlmResult."""
        del pool_id  # canonical wire is queue-group dispatched, no per-worker routing

        if self._cb.is_open():
            return LlmResult(
                error="LLM circuit open — adapter temporarily unavailable",
                retryable=True,
            )

        payload, trace_id = self._build_request(
            text, model_cfg, system_prompt, messages, stream=False
        )
        try:
            resp = await self._walk_registry_request(payload)
        except LlmUnavailableError as exc:
            return LlmResult(error=str(exc), retryable=True)
        self._cb.record_success()
        return LlmResult(
            result=resp.text or "",
            session_id=trace_id,
            error="",
            retryable=True,
        )

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
    # Internal — transport
    # ------------------------------------------------------------------

    async def _stream_gen(
        self,
        text: str,
        model_cfg: "ModelConfig",
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Async generator: yield LlmEvents from an ephemeral inbox subscription."""
        if self._cb.is_open():
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                error_text="LLM circuit open — adapter temporarily unavailable",
            )
            return

        candidates = self._registry.ordered_by_score()
        if not candidates:
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=0,
                error_text="LLM: no live worker (heartbeat stale >15s)",
            )
            return

        payload, _trace_id = self._build_request(
            text, model_cfg, system_prompt, messages, stream=True
        )

        inbox = self._nc.new_inbox()
        sub = await self._nc.subscribe(inbox)
        try:
            try:
                await self._nc.publish(SUBJECTS.generate_request, payload, reply=inbox)
            except NoRespondersError as exc:
                self._cb.record_failure()
                yield ResultLlmEvent(
                    is_error=True,
                    duration_ms=0,
                    error_text=f"NATS no responders: {exc}",
                )
                return
            except nats.errors.Error as exc:
                self._cb.record_failure()
                yield ResultLlmEvent(
                    is_error=True,
                    duration_ms=0,
                    error_text=f"NATS transport error: {exc}",
                )
                return

            try:
                async for chunk in self._stream_from_worker(sub, candidates):
                    if chunk.is_error:
                        self._cb.record_failure()
                        yield ResultLlmEvent(
                            is_error=True,
                            duration_ms=chunk.duration_ms or 0,
                            error_text=chunk.error or "LLM stream error",
                        )
                        return
                    if chunk.done:
                        self._cb.record_success()
                        yield ResultLlmEvent(
                            is_error=False,
                            duration_ms=chunk.duration_ms or 0,
                            cost_usd=None,
                        )
                        return
                    if chunk.delta:
                        yield TextLlmEvent(text=chunk.delta)
            except LlmUnavailableError as exc:
                yield ResultLlmEvent(
                    is_error=True,
                    duration_ms=0,
                    error_text=str(exc),
                )
        finally:
            await sub.unsubscribe()

    async def _stream_from_worker(
        self, sub: Any, candidates: list[Any]
    ) -> AsyncIterator[LlmChunkEvent]:
        """Yield raw LlmChunkEvent objects from the inbox subscription."""
        while True:
            try:
                msg = await sub.next_msg(timeout=self._timeout)
            except Exception as exc:
                if candidates:
                    self._registry.mark_stale(candidates[0].worker_id)
                self._cb.record_failure()
                raise LlmUnavailableError("LLM stream timed out") from exc
            try:
                chunk = LlmChunkEvent.model_validate_json(msg.data)
            except (ValidationError, ValueError) as exc:
                self._cb.record_failure()
                raise LlmUnavailableError("LLM stream: malformed chunk") from exc
            yield chunk
            if chunk.done or chunk.is_error:
                return

    async def _walk_registry_request(self, payload: bytes) -> LlmResponse:
        candidates = self._registry.ordered_by_score()
        if not candidates:
            raise LlmUnavailableError("LLM: no live worker (heartbeat stale >15s)")

        last_exc: Exception | None = None
        for worker in candidates:
            target = SUBJECTS.generate_request
            try:
                reply = await self._nc.request(target, payload, timeout=self._timeout)
                resp = LlmResponse.model_validate_json(reply.data)
                if not resp.ok:
                    self._cb.record_failure()
                    raise LlmUnavailableError(resp.error or "LLM generation failed")
                return resp
            except TimeoutError as exc:
                self._registry.mark_stale(worker.worker_id)
                last_exc = exc
                continue
            except (nats.errors.Error, TimeoutError) as exc:
                if isinstance(exc, NoRespondersError):
                    self._registry.mark_stale(worker.worker_id)
                    last_exc = exc
                    continue
                self._raise_nats_failure(exc, len(payload) / 1024)

        self._cb.record_failure()
        raise LlmUnavailableError("LLM: all workers unresponsive") from last_exc

    def _parse_reply(self, data: bytes) -> LlmResponse:
        try:
            return LlmResponse.model_validate_json(data)
        except (ValidationError, ValueError) as exc:
            self._cb.record_failure()
            raise LlmUnavailableError("LLM reply failed schema validation") from exc

    def _raise_nats_failure(self, exc: Exception, payload_kb: float) -> NoReturn:
        if isinstance(exc, LlmUnavailableError):
            raise exc
        if "max_payload" in str(exc).lower():
            log.error("LLM payload too large (%.0f KB)", payload_kb)
            self._cb.record_failure()
            raise LlmUnavailableError("LLM request payload too large") from exc
        log.warning("LLM adapter unreachable: %s: %s", type(exc).__name__, exc)
        self._cb.record_failure()
        raise LlmUnavailableError("LLM adapter unreachable") from exc
